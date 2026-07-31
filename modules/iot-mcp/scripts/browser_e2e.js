async (page) => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };

  const browserErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  const baseUrl = await page.evaluate(() => window.location.origin);
  await page.getByLabel("Admin Token").waitFor();
  await page.getByLabel("Admin Token").fill("browser-admin-token");
  await page.getByRole("button", { name: "建立安全 Session" }).click();
  await page.getByRole("heading", { name: "先处理失联与待确认" }).waitFor();
  browserErrors.length = 0;

  await page.goto(`${baseUrl}/devices`);
  const lightRow = page.getByRole("row").filter({ hasText: "Desk light" });
  await lightRow.getByRole("link", { name: "打开控制台" }).click();
  await page.getByRole("heading", { name: "Desk light" }).waitFor();
  assert(
    (await page.locator(".device-header").innerText()).includes("mock-light / v1"),
    "Desk light did not render its exact bound model",
  );
  const brightnessControl = page
    .locator("form.control-row")
    .filter({ hasText: "Brightness" });
  await brightnessControl.getByLabel("目标值").fill("72");
  await brightnessControl.getByRole("button", { name: "直接写入" }).click();
  await brightnessControl.getByText(/执行结果：succeeded/).waitFor();

  await page.goto(`${baseUrl}/devices`);
  const doorRow = page.getByRole("row").filter({ hasText: "Front door" });
  await doorRow.getByRole("link", { name: "打开控制台" }).click();
  await page.getByRole("heading", { name: "Front door" }).waitFor();
  assert(
    (await page.locator(".device-header").innerText()).includes("mock-lock / v1"),
    "Front door did not render its exact bound model",
  );
  const lockControl = page
    .locator("form.control-row")
    .filter({ hasText: "LockState" });
  await lockControl.getByLabel("目标值").selectOption("UNLOCK");
  await lockControl.getByRole("button", { name: "直接写入" }).click();
  await lockControl.getByText(/执行结果：succeeded/).waitFor();

  const machineHeaders = {
    Authorization: "Bearer browser-machine-token",
    "Content-Type": "application/json",
  };
  const devicesResponse = await page.request.get(`${baseUrl}/api/v1/devices`, {
    headers: machineHeaders,
  });
  assert(devicesResponse.ok(), `Device inventory failed: ${devicesResponse.status()}`);
  const devices = await devicesResponse.json();
  const door = devices.find((device) => device.display_name === "Front door");
  assert(door, "Front door was absent from the synchronized inventory");
  const pendingResponse = await page.request.post(
    `${baseUrl}/api/v1/devices/${door.device_id}/properties:write`,
    {
      headers: {
        ...machineHeaders,
        "Idempotency-Key": "browser-autonomous-lock",
      },
      data: { values: { LockState: "LOCK" } },
    },
  );
  assert(
    pendingResponse.status() === 202,
    `Autonomous high-risk request was not pending: ${pendingResponse.status()}`,
  );
  const pendingOperation = await pendingResponse.json();
  assert(
    pendingOperation.status === "pending_confirmation",
    `Unexpected autonomous operation status: ${pendingOperation.status}`,
  );

  await page.goto(`${baseUrl}/operations`);
  const confirmation = page
    .locator("article.confirmation-row")
    .filter({ hasText: "写入属性：LockState=LOCK" });
  await confirmation.waitFor();
  await confirmation.getByRole("button", { name: "批准此操作" }).click();
  await page.getByText(/决定已提交：批准 · succeeded/).waitFor();
  await page.getByText("当前没有待确认操作").waitFor();

  await page.reload();
  await page.getByRole("heading", { name: "操作与确认" }).waitFor();
  assert(
    (await page.getByLabel("Admin Token").count()) === 0,
    "Reload did not recover the HttpOnly browser session",
  );
  await page.getByText("写入属性：LockState=LOCK").first().waitFor();

  await page.setViewportSize({ width: 390, height: 844 });
  const widths = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  assert(
    widths.document === widths.viewport && widths.body === widths.viewport,
    `Mobile viewport overflows horizontally: ${JSON.stringify(widths)}`,
  );
  assert(
    browserErrors.length === 0,
    `Browser emitted errors after authentication: ${browserErrors.join(" | ")}`,
  );
  await page.screenshot({ path: "browser-e2e-final.png" });
}
