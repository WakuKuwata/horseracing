import { useState } from "react";

export const FORECAST_MARKS = [
  { value: "", label: "無印" },
  { value: "◎", label: "◎ 本命" },
  { value: "○", label: "○ 対抗" },
  { value: "▲", label: "▲ 単穴" },
  { value: "△", label: "△ 連下" },
  { value: "☆", label: "☆ 注目" },
  { value: "消", label: "消 見送り" },
] as const;

export type ForecastMark = (typeof FORECAST_MARKS)[number]["value"];
export type ForecastMarks = Record<string, ForecastMark>;

const READ_ERROR = "保存した印を読み込めませんでした。この画面で印を付け直せます。";
const WRITE_ERROR = "印をこのブラウザに保存できませんでした。この画面内では保持していますが、再読み込みすると失われます。";

export function isForecastMark(value: unknown): value is ForecastMark {
  return FORECAST_MARKS.some((mark) => mark.value === value);
}

function readMarks(key: string): { marks: ForecastMarks; error: string | null } {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return { marks: {}, error: null };
    const saved = JSON.parse(raw);
    if (saved?.version !== 1 || !saved.marks || typeof saved.marks !== "object" || Array.isArray(saved.marks)) {
      return { marks: {}, error: READ_ERROR };
    }
    const entries = Object.entries(saved.marks);
    if (!entries.every(([, mark]) => isForecastMark(mark))) {
      return { marks: {}, error: READ_ERROR };
    }
    return { marks: Object.fromEntries(entries) as ForecastMarks, error: null };
  } catch {
    return { marks: {}, error: READ_ERROR };
  }
}

/** Used inside the race-keyed detail component: never write until the user edits a mark. */
export function useForecastMarks(raceId: string) {
  const key = `horseracing:forecast-marks:v1:${raceId}`;
  const [state, setState] = useState(() => readMarks(key));

  function setMark(horseId: string, mark: ForecastMark) {
    if (!isForecastMark(mark)) return;
    const marks = { ...state.marks, [horseId]: mark };
    if (mark === "") delete marks[horseId];
    let error: string | null = null;
    try {
      window.localStorage.setItem(key, JSON.stringify({ version: 1, marks }));
    } catch {
      error = WRITE_ERROR;
    }
    setState({ marks, error });
  }

  return { marks: state.marks, error: state.error, setMark };
}
