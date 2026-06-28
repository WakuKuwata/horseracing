import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataBackingBadge } from "./DataBackingBadge";

describe("DataBackingBadge", () => {
  it("renders a NEUTRAL factual history-volume label (not weak/strong confidence)", () => {
    render(<DataBackingBadge band="few" />);
    const badge = screen.getByText("出走歴 少");
    expect(badge).toHaveAttribute("data-prior-starts-band", "few");
    // factual wording only — never confidence/avoid language (codex R6)
    expect(badge.textContent).not.toMatch(/weak|strong|危険|避け|信頼度|確信/);
    // the title states it is NOT a prediction-accuracy guarantee
    expect(badge.getAttribute("title")).toMatch(/的中確信|保証ではありません/);
  });

  it("does not use win/loss colour semantics", () => {
    const { container } = render(<DataBackingBadge band="many" />);
    expect(container.querySelector(".good, .bad, .danger, .success")).toBeNull();
    expect(screen.getByText("出走歴 多")).toBeInTheDocument();
  });

  it("renders a placeholder when absent (US3 deferred / unknown)", () => {
    const { container } = render(<DataBackingBadge band={null} />);
    expect(container.textContent).toBe("—");
  });
});
