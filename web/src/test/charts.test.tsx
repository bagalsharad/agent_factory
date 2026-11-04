import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActivityChart } from "../components/charts";

const ITEMS = [
  { start: "2026-11-03T10:00:00Z", completed: 3, failed: 1, other: 0, tokens: 120, p95_latency_ms: 950 },
  { start: "2026-11-03T11:00:00Z", completed: 0, failed: 0, other: 2, tokens: 0, p95_latency_ms: null },
];

describe("ActivityChart", () => {
  it("labels the series and offers a table view with every value", () => {
    render(<ActivityChart items={ITEMS} hours={24} />);
    expect(screen.getByRole("img", { name: /6 in total/ })).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Table/ }));
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("950 ms");
  });

  it("says so when the window has no runs", () => {
    render(<ActivityChart items={[{ ...ITEMS[1], other: 0 }]} hours={24} />);
    expect(screen.getByText("No production runs in this window")).toBeInTheDocument();
  });
});
