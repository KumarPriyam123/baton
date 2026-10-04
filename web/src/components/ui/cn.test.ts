import { describe, expect, it } from "vitest";
import { cn } from "./cn";

describe("cn with Baton's custom tokens", () => {
  it("keeps a custom text size next to a colour class", () => {
    expect(cn("text-meta", "text-pencil")).toBe("text-meta text-pencil");
    expect(cn("text-pencil", "text-meta")).toBe("text-pencil text-meta");
    expect(cn("text-figure text-ink")).toBe("text-figure text-ink");
  });

  it("still lets a later size replace an earlier one", () => {
    expect(cn("text-body", "text-meta")).toBe("text-meta");
    expect(cn("text-small", "text-sm")).toBe("text-sm");
  });

  it("still lets a later colour replace an earlier one", () => {
    expect(cn("text-ink", "text-pencil")).toBe("text-pencil");
  });

  it("keeps a custom weight next to a custom size, and replaces a weight with a weight", () => {
    expect(cn("font-strong", "text-meta")).toBe("font-strong text-meta");
    expect(cn("font-body", "font-strong")).toBe("font-strong");
    expect(cn("font-strong", "font-bold")).toBe("font-bold");
  });
});
