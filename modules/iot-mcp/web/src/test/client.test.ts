import { ApiError, HttpApiClient } from "../api/client";

describe("HttpApiClient authentication", () => {
  const localStorageMock = {
    getItem: vi.fn(),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  };
  const sessionStorageMock = {
    getItem: vi.fn(),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  };

  beforeEach(() => {
    vi.stubGlobal("localStorage", localStorageMock);
    vi.stubGlobal("sessionStorage", sessionStorageMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("exchanges the admin token once, never persists it, and sends CSRF on writes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ csrf_token: "csrf-1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ operation_id: "op-1", status: "succeeded" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HttpApiClient();

    await client.createSession("admin-secret");
    await client.writeProperties("device-1", { PowerSwitch: true });

    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      credentials: "include",
      headers: expect.objectContaining({ Authorization: "Bearer admin-secret" }),
    });
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      credentials: "include",
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-1" }),
    });
    expect(localStorageMock.setItem).not.toHaveBeenCalled();
    expect(sessionStorageMock.setItem).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls[1][1]?.headers).not.toHaveProperty("Authorization");
  });

  it("throws a typed error with the stable backend error fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "provider_unavailable",
              message: "live provider state is unavailable",
              retryable: true,
              request_id: "req-1",
            },
          }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(new HttpApiClient().listDevices()).rejects.toEqual(
      expect.objectContaining({
        name: "ApiError",
        message: "live provider state is unavailable",
        code: "provider_unavailable",
        retryable: true,
        requestId: "req-1",
        status: 503,
      }),
    );
  });

  it("restores CSRF from the cookie bootstrap and sends it on authenticated POSTs", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ csrf_token: "restored-csrf", expires_at: "2026-07-29T10:00:00Z" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ valid: true, model_version_id: "model-1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HttpApiClient();

    await client.bootstrapSession();
    await client.validateThingModel("model-1");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/auth/session");
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({
      "X-CSRF-Token": "restored-csrf",
    });
  });

  it("clears CSRF and notifies the app when a session becomes invalid", async () => {
    const onInvalid = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ csrf_token: "csrf", expires_at: "future" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ error: { code: "session_invalid", message: "expired" } }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ valid: true, model_version_id: "model-1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HttpApiClient("/api/v1", onInvalid);

    await client.bootstrapSession();
    await expect(client.validateThingModel("model-1")).rejects.toBeInstanceOf(ApiError);
    await client.validateThingModel("model-1");

    expect(onInvalid).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[2][1]?.headers).not.toHaveProperty("X-CSRF-Token");
  });

  it("sends authenticated decision, invoke, and sync POST contracts", async () => {
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        json({ csrf_token: "csrf-post", expires_at: "2026-07-29T10:00:00Z" }),
      )
      .mockResolvedValueOnce(
        json({ operation_id: "op-decision", device_id: "door", status: "succeeded" }),
      )
      .mockResolvedValueOnce(
        json({ operation_id: "op-invoke", device_id: "door", status: "succeeded" }),
      )
      .mockResolvedValueOnce(
        json({ discovered: 3, upserted: 2, missing: 1, snapshots: 2 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new HttpApiClient();

    await client.bootstrapSession();
    await client.decideConfirmation("confirm 1", "approve", "hash-1");
    await client.invokeService("device 1", "Temporary Unlock", { duration: 10 });
    await client.syncProvider("home assistant");

    expect(fetchMock.mock.calls.slice(1).map(([url]) => url)).toEqual([
      "/api/v1/confirmations/confirm%201:approve",
      "/api/v1/devices/device%201/services/Temporary%20Unlock:invoke",
      "/api/v1/providers/home%20assistant:sync",
    ]);
    expect(fetchMock.mock.calls.slice(1).map(([, init]) => init?.method)).toEqual([
      "POST",
      "POST",
      "POST",
    ]);
    for (const [, init] of fetchMock.mock.calls.slice(1)) {
      expect(init?.headers).toMatchObject({ "X-CSRF-Token": "csrf-post" });
    }
    expect(fetchMock.mock.calls[1][1]?.body).toBe(
      JSON.stringify({ action_hash: "hash-1" }),
    );
    expect(fetchMock.mock.calls[2][1]?.body).toBe(
      JSON.stringify({ inputs: { duration: 10 } }),
    );
    expect(fetchMock.mock.calls[2][1]?.headers).toEqual(
      expect.objectContaining({ "Idempotency-Key": expect.any(String) }),
    );
  });

  it("uses the draft model lifecycle API contracts", async () => {
    const tsl = {
      schema: "https://iotx-tsl.example/schema.json",
      profile: { productKey: "manual-model" },
      properties: [],
      services: [],
      events: [],
    };
    const model = {
      model_version_id: "model-1",
      product_id: "product-1",
      version: 1,
      status: "draft",
      tsl_json: tsl,
      created_at: "2026-07-30T00:00:00Z",
    };
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        json({ csrf_token: "csrf-model", expires_at: "future" }),
      )
      .mockResolvedValueOnce(
        json({
          product: {
            product_id: "product-1",
            product_key: "manual-model",
            name: "Manual model",
            source: "http",
            capability_fingerprint: "fingerprint",
            created_at: "2026-07-30T00:00:00Z",
          },
          model,
        }),
      )
      .mockResolvedValueOnce(json({ ...model, status: "active" }))
      .mockResolvedValueOnce(json({ ...model, status: "archived" }))
      .mockResolvedValueOnce(json(tsl));
    vi.stubGlobal("fetch", fetchMock);
    const client = new HttpApiClient();

    await client.bootstrapSession();
    await client.importThingModel("Manual model", tsl);
    await client.publishThingModel("model-1");
    await client.archiveThingModel("model-1");
    await client.exportThingModel("model-1");

    expect(fetchMock.mock.calls.slice(1).map(([url]) => url)).toEqual([
      "/api/v1/thing-models",
      "/api/v1/thing-models/model-1:publish",
      "/api/v1/thing-models/model-1:archive",
      "/api/v1/thing-models/model-1:export",
    ]);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ name: "Manual model", tsl }),
      headers: expect.objectContaining({
        "X-CSRF-Token": "csrf-model",
      }),
    });
    expect(fetchMock.mock.calls[4][1]?.method).toBe("GET");
  });
});
