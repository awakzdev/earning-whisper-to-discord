// worker.js
async function verifyDiscord(req, publicKeyHex) {
  const sig = req.headers.get("x-signature-ed25519");
  const ts  = req.headers.get("x-signature-timestamp");
  const body = await req.text();

  const hexToU8 = (h) => new Uint8Array(h.match(/.{1,2}/g).map(b => parseInt(b, 16)));
  const key = await crypto.subtle.importKey("raw", hexToU8(publicKeyHex), { name: "Ed25519" }, false, ["verify"]);
  const ok  = await crypto.subtle.verify("Ed25519", hexToU8(sig), new TextEncoder().encode(ts + body), key);
  return { ok, body };
}

async function triggerGitHub(env, reason) {
  const res = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GH_TOKEN}`,
      "Accept": "application/vnd.github+json"
    },
    body: JSON.stringify({ ref: env.GH_REF, inputs: { reason } })
  });
  return res.ok ? "Triggered ✅" : `Trigger failed: ${res.status}`;
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);

    // Optional: simple GET trigger for you: https://.../check?key=SECRET
    if (req.method === "GET" && url.pathname === "/check") {
      if (!env.SECRET_KEY || url.searchParams.get("key") !== env.SECRET_KEY) {
        return new Response("forbidden", { status: 403 });
      }
      const msg = await triggerGitHub(env, "bridge");
      return new Response(msg, { status: 200 });
    }

    // Slash-command interactions hit POST /interactions
    if (req.method !== "POST" || url.pathname !== "/interactions") {
      return new Response("ok", { status: 200 });
    }

    const { ok, body } = await verifyDiscord(req, env.DISCORD_PUBLIC_KEY);
    if (!ok) return new Response("bad signature", { status: 401 });

    const data = JSON.parse(body);

    // Discord PING
    if (data.type === 1) {
      return new Response(JSON.stringify({ type: 1 }), { headers: { "Content-Type": "application/json" } });
    }

    // Slash command: /check
    if (data.type === 2 && data.data?.name === "check") {
      const msg = await triggerGitHub(env, "discord-slash");
      return new Response(JSON.stringify({ type: 4, data: { flags: 64, content: msg } }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    return new Response("unsupported", { status: 400 });
  }
}
