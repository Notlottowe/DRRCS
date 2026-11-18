// Cloudflare-owned pages.dev API entry. Business logic is in the Python Worker.
export default {
  fetch(request, env) {
    return env.BACKEND.fetch(request);
  }
};
