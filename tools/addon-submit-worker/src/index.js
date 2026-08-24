/**
 * IchaLaunch catalog suggestion Worker.
 *
 * Accepts POST JSON from the launcher (no GitHub credentials in the client),
 * validates the payload, applies basic size/rate limits, then opens GitHub
 * Issues on brutaliccus/IchaLaunch for the whole fork network:
 *   - resolve root (source/parent if submission is a fork, else the repo itself)
 *   - list active (non-archived/disabled) forks of that root (cap MAX_FORKS)
 *   - open one `[catalog]` issue for root + each fork (+ submitted if missing)
 *
 * Fan-out is best-effort at submit time (not on catalog-approved). The client
 * still receives one success response for the primary submission issue.
 */

const MAX_BODY_BYTES = 16 * 1024;
const MAX_NAME = 120;
const MAX_DESC = 4000;
const MAX_FOLDER = 80;
const MAX_CATEGORY = 64;
const MAX_CLIENT_ID = 64;
const RATE_WINDOW_MS = 60 * 60 * 1000;
const RATE_MAX_PER_KEY = 8;
const MAX_FORKS = 40;
const FORKS_PER_PAGE = 100;
const FORKS_MAX_PAGES = 5; // 500 listed, then score/cap to MAX_FORKS
const ADDONS_JSON_URL =
  "https://raw.githubusercontent.com/brutaliccus/IchaLaunch/master/ichalaunch/data/addons.json";

/** @type {Map<string, number[]>} */
const rateBuckets = new Map();

const REPO_RE = /^https:\/\/github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\/?$/i;

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function clientIp(request) {
  return (
    request.headers.get("cf-connecting-ip") ||
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    "unknown"
  );
}

function rateLimited(key) {
  const now = Date.now();
  const prev = rateBuckets.get(key) || [];
  const kept = prev.filter((t) => now - t < RATE_WINDOW_MS);
  if (kept.length >= RATE_MAX_PER_KEY) {
    rateBuckets.set(key, kept);
    return true;
  }
  kept.push(now);
  rateBuckets.set(key, kept);
  if (rateBuckets.size > 5000) {
    const oldest = rateBuckets.keys().next().value;
    if (oldest !== undefined) rateBuckets.delete(oldest);
  }
  return false;
}

function normalizeRepo(raw) {
  const s = String(raw || "").trim().replace(/\.git$/i, "");
  const m = s.match(REPO_RE);
  if (!m) return null;
  return `https://github.com/${m[1]}/${m[2]}`;
}

function repoSlug(repoUrl) {
  const parts = String(repoUrl || "").split("/");
  return parts[parts.length - 1] || "";
}

function ownerRepo(repoUrl) {
  const m = String(repoUrl || "").match(REPO_RE);
  if (!m) return null;
  return { owner: m[1], name: m[2] };
}

function browseUrl(owner, name) {
  return `https://github.com/${owner}/${name}`;
}

function validatePayload(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return { error: "JSON object required." };
  }
  const repo = normalizeRepo(data.repo);
  if (!repo) {
    return { error: "repo must be https://github.com/owner/repo." };
  }
  const slug = repoSlug(repo);
  const name = String(data.name || "").trim() || slug;
  const category = String(data.category || "").trim();
  const description = String(data.description || "").trim();
  const folder = String(data.folder || "").trim() || slug;
  const launcherVersion = String(data.launcher_version || "").trim().slice(0, 32);
  const clientId = String(data.client_id || "").trim().slice(0, MAX_CLIENT_ID);

  if (!category) return { error: "category is required." };
  if (name.length > MAX_NAME) return { error: `name too long (max ${MAX_NAME}).` };
  if (category.length > MAX_CATEGORY) {
    return { error: `category too long (max ${MAX_CATEGORY}).` };
  }
  if (description.length > MAX_DESC) {
    return { error: `description too long (max ${MAX_DESC}).` };
  }
  if (folder.length > MAX_FOLDER) {
    return { error: `folder too long (max ${MAX_FOLDER}).` };
  }

  return {
    payload: {
      repo,
      name,
      category,
      description,
      folder,
      launcher_version: launcherVersion,
      client_id: clientId,
    },
  };
}

function ghHeaders(token) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "User-Agent": "IchaLaunch-addon-submit-worker",
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

function issueBody(p, extraNote) {
  const hasDesc = Boolean(p.description);
  const entry = {
    name: p.name || undefined,
    repo: p.repo,
    category: p.category,
    source: "community",
    folder: p.folder || undefined,
  };
  const lines = [
    "### Catalog suggestion (from IchaLaunch)",
    "",
    `- **repo:** ${p.repo}`,
    `- **name:** ${p.name || "(none)"}`,
    `- **category:** ${p.category}`,
    `- **description:** ${hasDesc ? "(README excerpt below)" : "(none)"}`,
    `- **folder:** ${p.folder || "(none)"}`,
    `- **launcher_version:** ${p.launcher_version || "(unknown)"}`,
    `- **client_id:** ${p.client_id || "(none)"}`,
    "",
  ];
  if (extraNote) {
    lines.push(extraNote, "");
  }
  lines.push(
    "Suggested `addons.json` entry:",
    "",
    "```json",
    JSON.stringify(entry, null, 2),
    "```"
  );
  if (hasDesc) {
    lines.push(
      "",
      "### README excerpt",
      "",
      "````markdown",
      p.description,
      "````"
    );
  }
  lines.push(
    "",
    "Maintainers: review, then label this issue `catalog-approved`.",
    "A GitHub Action opens a PR that edits `ichalaunch/data/addons.json`,",
    "squash-merges it to `master`, and closes this issue."
  );
  return lines.join("\n");
}

async function ghJson(token, url) {
  const res = await fetch(url, { headers: ghHeaders(token) });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  return { res, data, text };
}

/**
 * Resolve network root: prefer GitHub `source` (ultimate upstream), else
 * `parent`, else the repo itself.
 */
function rootFromMeta(meta) {
  const src = meta && meta.fork && meta.source;
  const parent = meta && meta.fork && meta.parent;
  const node = src || parent || meta;
  const full = String(node?.full_name || "").trim();
  const html = normalizeRepo(String(node?.html_url || "").trim());
  if (html) {
    const parts = ownerRepo(html);
    if (parts) return { ...parts, url: html, description: String(node.description || "").trim() };
  }
  if (full.includes("/")) {
    const [owner, name] = full.split("/", 2);
    return {
      owner,
      name,
      url: browseUrl(owner, name),
      description: String(node?.description || "").trim(),
    };
  }
  return null;
}

async function fetchRepoMeta(token, owner, name) {
  const { res, data } = await ghJson(token, `https://api.github.com/repos/${owner}/${name}`);
  if (!res.ok || !data || typeof data !== "object") {
    return { ok: false, status: res.status, data: null };
  }
  return { ok: true, status: res.status, data };
}

function forkScore(item) {
  const stars = Number(item.stargazers_count || 0);
  const watchers = Number(item.watchers_count || item.watchers || 0);
  const pushed = Date.parse(String(item.pushed_at || "")) || 0;
  return { score: stars + watchers, pushed };
}

/**
 * Active forks of root, sorted by stars/activity, capped (enrich_catalog_forks ideas).
 */
async function listActiveForks(token, owner, name, rootUrl) {
  const candidates = [];
  const rootLower = rootUrl.toLowerCase();

  for (let page = 1; page <= FORKS_MAX_PAGES; page++) {
    const url =
      `https://api.github.com/repos/${owner}/${name}/forks` +
      `?per_page=${FORKS_PER_PAGE}&sort=stargazers&page=${page}`;
    const { res, data } = await ghJson(token, url);
    if (res.status === 404 || res.status === 451) break;
    if (!res.ok || !Array.isArray(data)) break;
    if (data.length === 0) break;

    for (const item of data) {
      if (!item || typeof item !== "object") continue;
      if (item.archived || item.disabled) continue;
      const html = normalizeRepo(String(item.html_url || ""));
      if (!html || html.toLowerCase() === rootLower) continue;
      const full = String(item.full_name || "").trim();
      const parts = ownerRepo(html);
      if (!parts) continue;
      const { score, pushed } = forkScore(item);
      candidates.push({
        owner: parts.owner,
        name: parts.name,
        url: html,
        full_name: full || `${parts.owner}/${parts.name}`,
        description: String(item.description || "").trim().slice(0, MAX_DESC),
        score,
        pushed,
      });
    }
    if (data.length < FORKS_PER_PAGE) break;
  }

  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.pushed !== a.pushed) return b.pushed - a.pushed;
    return String(a.full_name).localeCompare(String(b.full_name));
  });
  return candidates.slice(0, MAX_FORKS);
}

async function loadCatalogRepoSet() {
  const found = new Set();
  try {
    const res = await fetch(ADDONS_JSON_URL, {
      headers: { "User-Agent": "IchaLaunch-addon-submit-worker" },
    });
    if (!res.ok) return found;
    const data = await res.json();
    if (!Array.isArray(data)) return found;
    for (const item of data) {
      if (!item || typeof item !== "object") continue;
      const primary = normalizeRepo(String(item.repo || ""));
      if (primary) found.add(primary.toLowerCase());
      const forks = item.forks;
      if (!Array.isArray(forks)) continue;
      for (const fork of forks) {
        if (!fork || typeof fork !== "object") continue;
        const f = normalizeRepo(String(fork.repo || ""));
        if (f) found.add(f.toLowerCase());
      }
    }
  } catch {
    // Best-effort: if catalog fetch fails, still open issues (may duplicate).
  }
  return found;
}

/**
 * Scan recent open `[catalog]` issues for repo URLs already requested.
 */
async function loadOpenSuggestionRepos(token, githubRepo) {
  const found = new Set();
  try {
    const q = encodeURIComponent(`repo:${githubRepo} is:issue is:open in:title [catalog]`);
    const { res, data } = await ghJson(
      token,
      `https://api.github.com/search/issues?q=${q}&per_page=50`
    );
    if (!res.ok || !data || !Array.isArray(data.items)) return found;
    const urlRe = /https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+/gi;
    for (const issue of data.items) {
      const blob = `${issue.title || ""}\n${issue.body || ""}`;
      const matches = blob.match(urlRe) || [];
      for (const raw of matches) {
        const n = normalizeRepo(raw);
        if (n) found.add(n.toLowerCase());
      }
    }
  } catch {
    // Optional dedupe — ignore failures.
  }
  return found;
}

async function createOneIssue(token, githubRepo, payload, titleName, extraNote) {
  const title = `[catalog] ${titleName}`.slice(0, 200);
  const res = await fetch(`https://api.github.com/repos/${githubRepo}/issues`, {
    method: "POST",
    headers: {
      ...ghHeaders(token),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title,
      body: issueBody(payload, extraNote),
    }),
  });

  const text = await res.text();
  let data = {};
  try {
    data = JSON.parse(text);
  } catch {
    data = {};
  }

  if (!res.ok) {
    const msg =
      data.message ||
      (res.status === 401 || res.status === 403
        ? "GitHub auth failed on server."
        : `GitHub issue create failed (HTTP ${res.status}).`);
    return { ok: false, status: res.status, message: msg, repo: payload.repo };
  }

  return {
    ok: true,
    status: 200,
    issue_url: data.html_url || null,
    issue_number: data.number || null,
    repo: payload.repo,
  };
}

/**
 * Build fork-network candidate list and open catalog issues.
 * Returns the primary (submitted) issue result for the HTTP response.
 */
async function submitWithForkFanout(env, payload) {
  const token = String(env.GITHUB_TOKEN || "").trim();
  const githubRepo = String(env.GITHUB_REPO || "brutaliccus/IchaLaunch").trim();
  if (!token) {
    return { ok: false, status: 500, message: "Server misconfigured (missing token)." };
  }
  if (!/^[^/\s]+\/[^/\s]+$/.test(githubRepo)) {
    return { ok: false, status: 500, message: "Server misconfigured (bad repo)." };
  }

  const submitted = ownerRepo(payload.repo);
  if (!submitted) {
    return { ok: false, status: 400, message: "Invalid repo URL." };
  }

  // Resolve root of the fork network.
  let root = {
    owner: submitted.owner,
    name: submitted.name,
    url: payload.repo,
    description: "",
  };
  const meta = await fetchRepoMeta(token, submitted.owner, submitted.name);
  if (meta.ok && meta.data) {
    const resolved = rootFromMeta(meta.data);
    if (resolved) root = resolved;
  } else {
    console.log(
      `fork-fanout: repo meta failed for ${submitted.owner}/${submitted.name} HTTP ${meta.status}; treating as root`
    );
  }

  let forks = [];
  try {
    forks = await listActiveForks(token, root.owner, root.name, root.url);
  } catch (err) {
    console.log(`fork-fanout: list forks failed: ${err}`);
    forks = [];
  }

  /** @type {Map<string, { url: string, owner: string, name: string, full_name: string, description: string, isSubmitted: boolean, isRoot: boolean }>} */
  const network = new Map();

  const addNode = (node, flags) => {
    const url = normalizeRepo(node.url);
    if (!url) return;
    const key = url.toLowerCase();
    const prev = network.get(key);
    network.set(key, {
      url,
      owner: node.owner,
      name: node.name,
      full_name: node.full_name || `${node.owner}/${node.name}`,
      description: String(node.description || "").trim().slice(0, MAX_DESC),
      isSubmitted: Boolean(prev?.isSubmitted || flags.isSubmitted),
      isRoot: Boolean(prev?.isRoot || flags.isRoot),
    });
  };

  addNode(
    {
      owner: root.owner,
      name: root.name,
      url: root.url,
      full_name: `${root.owner}/${root.name}`,
      description: root.description,
    },
    { isRoot: true, isSubmitted: root.url.toLowerCase() === payload.repo.toLowerCase() }
  );

  for (const f of forks) {
    addNode(f, {
      isRoot: false,
      isSubmitted: f.url.toLowerCase() === payload.repo.toLowerCase(),
    });
  }

  // Always include the originally submitted repo.
  addNode(
    {
      owner: submitted.owner,
      name: submitted.name,
      url: payload.repo,
      full_name: `${submitted.owner}/${submitted.name}`,
      description: payload.description || "",
    },
    { isSubmitted: true, isRoot: payload.repo.toLowerCase() === root.url.toLowerCase() }
  );

  const [inCatalog, openSuggested] = await Promise.all([
    loadCatalogRepoSet(),
    loadOpenSuggestionRepos(token, githubRepo),
  ]);

  const candidates = [...network.values()].filter((n) => {
    const key = n.url.toLowerCase();
    if (inCatalog.has(key)) {
      console.log(`fork-fanout: skip already in catalog ${n.url}`);
      return false;
    }
    if (openSuggested.has(key)) {
      console.log(`fork-fanout: skip open suggestion ${n.url}`);
      return false;
    }
    return true;
  });

  if (candidates.length === 0) {
    return {
      ok: true,
      status: 200,
      message:
        "That repo (and its active fork network) is already in the catalog or has open catalog requests.",
      issue_url: null,
      opened: 0,
      skipped: network.size,
    };
  }

  // Prefer creating the submitted repo issue first (full user payload) for the response URL.
  candidates.sort((a, b) => {
    if (a.isSubmitted !== b.isSubmitted) return a.isSubmitted ? -1 : 1;
    if (a.isRoot !== b.isRoot) return a.isRoot ? -1 : 1;
    return a.full_name.localeCompare(b.full_name);
  });

  const networkNote = (node) => {
    const bits = [
      `_Fork network root: [${root.owner}/${root.name}](${root.url})._`,
    ];
    if (!node.isSubmitted) {
      bits.push(
        `_Opened automatically with a suggestion for [${payload.repo}](${payload.repo})._`
      );
    } else if (network.size > 1) {
      bits.push(
        `_Submit also opened catalog requests for other active forks of this network (best-effort)._`
      );
    }
    return bits.join(" ");
  };

  let primaryResult = null;
  let primaryFail = null;
  let opened = 0;
  const failures = [];

  for (const node of candidates) {
    const isPrimary = node.isSubmitted;
    const itemPayload = isPrimary
      ? { ...payload, repo: node.url }
      : {
          repo: node.url,
          name: node.name,
          category: payload.category,
          description: node.description || "",
          folder: node.name,
          launcher_version: payload.launcher_version || "fork-fanout",
          client_id: "(none)",
        };

    // Title: owner/repo for non-submitted (disambiguate same-named forks).
    const titleName = isPrimary
      ? payload.name || payload.folder || node.name
      : node.full_name;

    try {
      const result = await createOneIssue(
        token,
        githubRepo,
        itemPayload,
        titleName,
        networkNote(node)
      );
      if (!result.ok) {
        console.log(`fork-fanout: create failed ${node.url}: ${result.message}`);
        failures.push({ repo: node.url, error: result.message });
        if (isPrimary) primaryFail = result;
        continue;
      }
      opened += 1;
      if (isPrimary) {
        primaryResult = result;
      } else if (!primaryResult) {
        // Fallback response URL if submitted was skipped but others opened.
        primaryResult = result;
      }
    } catch (err) {
      console.log(`fork-fanout: create exception ${node.url}: ${err}`);
      failures.push({ repo: node.url, error: String(err) });
      if (isPrimary) {
        primaryFail = { ok: false, status: 502, message: String(err) };
      }
    }
  }

  if (opened === 0) {
    const failMsg =
      (primaryFail && primaryFail.message) ||
      (failures[0] && failures[0].error) ||
      "GitHub issue create failed.";
    return {
      ok: false,
      status: (primaryFail && primaryFail.status) || 502,
      message: failMsg,
    };
  }

  return {
    ok: true,
    status: 200,
    message:
      opened > 1
        ? `Suggestion submitted (${opened} catalog issues opened for this fork network). Maintainers will review them.`
        : "Suggestion submitted. Maintainers will review it.",
    issue_url: primaryResult?.issue_url || null,
    opened,
    failed: failures.length,
  };
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "POST, OPTIONS",
          "access-control-allow-headers": "content-type",
          "access-control-max-age": "86400",
        },
      });
    }

    if (request.method === "GET") {
      return jsonResponse(200, {
        ok: true,
        service: "ichalaunch-addon-submit",
        message: "POST JSON catalog suggestions here.",
        fork_fanout: true,
        max_forks: MAX_FORKS,
      });
    }

    if (request.method !== "POST") {
      return jsonResponse(405, { ok: false, error: "POST required." });
    }

    const len = Number(request.headers.get("content-length") || 0);
    if (len > MAX_BODY_BYTES) {
      return jsonResponse(413, { ok: false, error: "Body too large." });
    }

    const raw = await request.text();
    if (raw.length > MAX_BODY_BYTES) {
      return jsonResponse(413, { ok: false, error: "Body too large." });
    }

    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return jsonResponse(400, { ok: false, error: "Invalid JSON." });
    }

    const checked = validatePayload(data);
    if (checked.error) {
      return jsonResponse(400, { ok: false, error: checked.error });
    }

    const ip = clientIp(request);
    const cid = checked.payload.client_id || "anon";
    if (rateLimited(`${ip}|${cid}`)) {
      return jsonResponse(429, {
        ok: false,
        error: "Too many suggestions. Please wait and try again.",
      });
    }

    const result = await submitWithForkFanout(env, checked.payload);
    if (!result.ok) {
      return jsonResponse(result.status, {
        ok: false,
        error: result.message,
      });
    }
    return jsonResponse(200, {
      ok: true,
      message: result.message,
      issue_url: result.issue_url,
      opened: result.opened,
    });
  },
};
