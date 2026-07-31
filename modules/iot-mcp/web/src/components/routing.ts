export function consolePath(path: string): string {
  const demo = new URLSearchParams(window.location.search).get("demo") === "1";
  return demo ? `${path}${path.includes("?") ? "&" : "?"}demo=1` : path;
}
