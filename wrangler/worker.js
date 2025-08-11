// worker.js (hardened)
async function verifyDiscord(req, publicKeyHex) {
  const sigHex = req.headers.get("x-signature-ed25519") || "";
  const ts     = req.headers.get("x-signature-timestamp") || "";
  const body   = await req.text();

  const hexToU8 = (h) => new Uint8Array((h.match(/.{1,2}/g) || []).map(b => parseInt(b,16) || 0));
  try {
    const sig = hexToU8(sigHex);
    if (sig.length !== 64) return { ok: false, body };             // graceful reject
    const key = await crypto.subtle.importKey("raw", hexToU8(publicKeyHex), { name: "Ed25519" }, false, ["verify"]);
    const ok  = await crypto.subtle.verify("Ed25519", key, sig, new TextEncoder().encode(ts + body));
    return { ok, body };
  } catch (e) {
    return { ok: false, body };                                    // never throw to CF (avoids 1101)
  }
}

// was: async function triggerWorkflow(env, reason) {
async function triggerWorkflow(env, reason, inputs = {}) {
  const url = `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GH_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "EarningWhisperBot (+https://github.com/awakzdev/earning-whisper-to-discord)"
    },
    body: JSON.stringify({
      ref: env.GH_REF || "main",
      // GH requires *strings* for workflow_dispatch inputs
      inputs: Object.fromEntries(Object.entries(inputs).map(([k,v]) => [k, String(v)])),
    }),
  });
  const text = await res.text();
  if (!res.ok) return `Trigger failed: ${res.status} ${text?.slice(0,300)}`;
  return `Nothing will be posted if there's no new image to fetch.\nTriggered ✅ (${reason || "manual"})`;
}


export default {
  async fetch(req, env) {
    const url = new URL(req.url);

    if (req.method === "GET" && url.pathname === "/check") {
      if (!env.SECRET_KEY || url.searchParams.get("key") !== env.SECRET_KEY)
        return new Response("forbidden", { status: 403 });

      const inputs = {
        force: url.searchParams.get("force") === "1" ? "true" : "false",
        photo: url.searchParams.get("photo") === "0" ? "false" : "true",
      };
      return new Response(await triggerWorkflow(env, "link", inputs), { status: 200 });
    }

    if (req.method !== "POST" || url.pathname !== "/interactions") return new Response("ok", { status: 200 });

    const { ok, body } = await verifyDiscord(req, env.DISCORD_PUBLIC_KEY);
    if (!ok) return new Response("bad signature", { status: 401 });

    const data = JSON.parse(body);

    // PING
    if (data.type === 1) {
      return new Response(JSON.stringify({ type: 1 }), { headers: { "Content-Type": "application/json" }});
    }

        // Slash command
    if (data.type === 2) {
      const name = (data.data?.name || "").toLowerCase();

      // manual commands: /trigger forces; /check does not
      const inputs = name === "trigger"
        ? { force: "true",  photo: "true" }
        : { force: "false", photo: "true" };

      const msg = await triggerWorkflow(env, name, inputs); // <-- pass inputs
      return new Response(JSON.stringify({ type: 4, data: { flags: 64, content: msg }}), {
        headers: { "Content-Type": "application/json" }
      });
    }

    return new Response("unsupported", { status: 400 });
  }
}
