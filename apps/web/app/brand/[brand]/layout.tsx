import type { Metadata } from "next";

const BRAND_LABELS: Record<string, string> = {
  barbours: "BARBOURS",
  kokeshi: "KOKESHI",
  apice: "APICE",
  lescent: "LESCENT",
  rituaria: "RITUARIA",
};

export async function generateMetadata({
  params,
}: {
  params: Promise<{ brand: string }>;
}): Promise<Metadata> {
  const { brand } = await params;
  return { title: BRAND_LABELS[brand] ?? "Marca" };
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
