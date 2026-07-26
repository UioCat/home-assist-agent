async function readJson(response) {
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.detail || "Request failed");
  }
  return payload;
}

export async function getHealth() {
  return readJson(await fetch("/api/health"));
}

export async function submitCommand(payload) {
  return readJson(
    await fetch("/api/commands", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }),
  );
}
