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
});
