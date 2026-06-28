// JRA-VAN venue code (VV in the YYYYVVKKDDRR race_id) -> Japanese course name.
// Mirrors scrape/venues.py NETKEIBA_TO_JRAVAN_VENUE (JRA central courses share these codes).
// Unknown / local / overseas codes fall back to the raw code so nothing renders blank.

export const VENUE_NAMES: Record<string, string> = {
  "01": "札幌",
  "02": "函館",
  "03": "福島",
  "04": "新潟",
  "05": "東京",
  "06": "中山",
  "07": "中京",
  "08": "京都",
  "09": "阪神",
  "10": "小倉",
};

export function venueName(code: string | null | undefined): string {
  if (!code) return "—";
  return VENUE_NAMES[code] ?? code;
}
