import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HealthStatus } from "./HealthStatus";

function mockFetch(impl: () => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

describe("HealthStatus", () => {
  it("shows a loading message until the API answers", () => {
    mockFetch(() => new Promise<Response>(() => undefined));
    render(<HealthStatus />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking the API");
  });

  it("shows the status the API returned", async () => {
    mockFetch(() => Promise.resolve(Response.json({ status: "ok" })));
    render(<HealthStatus />);
    expect(await screen.findByText("API status: ok")).toBeInTheDocument();
  });

  it("shows an error message when the API answers with a failure", async () => {
    mockFetch(() => Promise.resolve(new Response("boom", { status: 502 })));
    render(<HealthStatus />);
    expect(await screen.findByText(/can't reach the API/i)).toBeInTheDocument();
  });

  it("shows an error message when the network fails", async () => {
    mockFetch(() => Promise.reject(new TypeError("network down")));
    render(<HealthStatus />);
    expect(await screen.findByText(/can't reach the API/i)).toBeInTheDocument();
  });
});
