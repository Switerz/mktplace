/** Marcas no escopo GoBeauté — espelha BRAND_LABELS em performance_service.py. */
export const ALL_BRAND_OPTIONS: readonly { value: string; label: string }[] = [
  { value: "apice", label: "ÁPICE" },
  { value: "barbours", label: "BARBOURS" },
  { value: "kokeshi", label: "KOKESHI" },
  { value: "lescent", label: "LESCENT" },
  { value: "rituaria", label: "RITUÁRIA" },
];

export const ALL_BRAND_KEYS: readonly string[] = ALL_BRAND_OPTIONS.map((b) => b.value);

export function brandLabel(brandKey: string): string {
  return ALL_BRAND_OPTIONS.find((b) => b.value === brandKey)?.label ?? brandKey.toUpperCase();
}
