/**
 * Feature 087: JRA frame colors for the betting-slip horse-number chips.
 * The frame number comes straight from the entry data (`HorseEntry.frame`) — it is NEVER
 * derived from horse_number/field size. Anything outside 1..8 degrades to the neutral chip.
 */
export function frameChipClass(frame: number | null | undefined): string {
  if (typeof frame === "number" && Number.isInteger(frame) && frame >= 1 && frame <= 8) {
    return `frame-chip frame-chip--${frame}`;
  }
  return "frame-chip frame-chip--none";
}
