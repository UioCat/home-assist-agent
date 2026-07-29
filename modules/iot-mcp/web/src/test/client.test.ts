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
});
