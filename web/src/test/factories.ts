import type { ItemOut } from "../lib/api";

/** A complete item with sensible defaults; override what the test cares about. */
export function makeItem(overrides: Partial<ItemOut> = {}): ItemOut {
  return {
    id: "00000000-0000-4000-8000-000000000001",
    key: "PAY-142",
    version: 7,
    last_event_id: 100,
    team: { key: "PAY", name: "Payments" },
    type: "payment_investigation",
    title: "Refund stuck for order 48213",
    description: "",
    status: "in_progress",
    priority: 1,
    confidential: false,
    requires_approval: true,
    requester: { id: "00000000-0000-4000-8000-0000000000a1", name: "Meera Iyer" },
    assignee: { id: "00000000-0000-4000-8000-0000000000a2", name: "Asha Rao" },
    resolution: null,
    resolution_note: null,
    duplicate_of: null,
    due_at: null,
    due_source: "auto",
    sla_breached: false,
    approval: null,
    next_step: { kind: "progress", label: "In progress with Asha", severity: "low" },
    allowed_actions: [],
    last_event: null,
    unread_since_event_id: null,
    watching: false,
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    resolved_at: null,
    closed_at: null,
    ...overrides,
  };
}
