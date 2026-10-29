import { toNumber } from "../../format";

type Row = Record<string, unknown>;

// A spreadsheet runs a cell that starts with one of these as a formula.
const FORMULA_START = /^[=+\-@\t\r]/;

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  let text = typeof value === "object" ? JSON.stringify(value) : String(value);
  // Stored text can start like a formula; a number cannot do harm and stays a number.
  if (FORMULA_START.test(text) && toNumber(value) === null) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/** The rows as the query returned them, unformatted and in the query's order. */
export function toCsv(columns: string[], rows: Row[]): string {
  return [columns.map(cell).join(","), ...rows.map((row) => columns.map((column) => cell(row[column])).join(","))].join("\r\n");
}

export function downloadCsv(name: string, columns: string[], rows: Row[]) {
  const safe = name.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 80) || "chart";
  // The byte order mark makes spreadsheet apps read the file as UTF-8.
  const blob = new Blob(["﻿", toCsv(columns, rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${safe}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}
