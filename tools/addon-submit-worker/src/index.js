/**
 * IchaLaunch catalog suggestion Worker.
 *
 * Accepts POST JSON from the launcher (no GitHub credentials in the client),
 * validates the payload, applies basic size/rate limits, and opens a GitHub
 * Issue on brutaliccus/IchaLaunch using Worker secrets.
 */

const MAX_BODY_BYTES = 16 * 1024;
const MAX_NAME = 120;
const MAX_DESC = 4000;
const MAX_FOLDER = 80;
const MAX_CATEGORY = 64;
const MAX_CLIENT_ID = 64;
const RATE_WINDOW_MS = 60 * 60 * 1000;
const RATE_MAX_PER_KEY = 8;

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
  // Bound memory in long-lived isolates
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

function issueBody(p) {
  const hasDesc = Boolean(p.description);
  const entry = {
    name: p.name || undefined,
    repo: p.repo,
    category: p.category,
    source: "community",
    folder: p.folder || undefined,
  };
  // Keep description out of the JSON fence (multi-line README breaks naive
  // ```json … ``` extraction). Full text lives in the README excerpt section.
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
    "Suggested `addons.json` entry:",
    "",
    "```json",
    JSON.stringify(entry, null, 2),
    "```",
  ];
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

async function createIssue(env, payload) {
  const token = String(env.GITHUB_TOKEN || "").trim();
  const repo = String(env.GITHUB_REPO || "brutaliccus/IchaLaunch").trim();
  if (!token) {
    return { ok: false, status: 500, message: "Server misconfigured (missing token)." };
  }
  if (!/^[^/\s]+\/[^/\s]+$/.test(repo)) {
    return { ok: false, status: 500, message: "Server misconfigured (bad repo)." };
  }

  const titleName = payload.name || payload.folder || repoSlug(payload.repo);
  const title = `[catalog] ${titleName}`.slice(0, 200);
  const res = await fetch(`https://api.github.com/repos/${repo}/issues`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "User-Agent": "IchaLaunch-addon-submit-worker",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title,
      body: issueBody(payload),
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
    return { ok: false, status: 502, message: msg };
  }

  return {
    ok: true,
    status: 200,
    message: "Suggestion submitted. Maintainers will review it.",
    issue_url: data.html_url || null,
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

    const result = await createIssue(env, checked.payload);
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
    });
  },
};
