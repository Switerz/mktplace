/**
 * Marca da Torre: a torre-farol de `app/icon.png`, o mesmo desenho da aba do
 * navegador. Decorativa (o nome "Torre de Controle" esta' ao lado), por isso
 * `alt` vazio. `<img>` simples: o arquivo ja' e' pequeno e servido pelo Next.
 */
export default function MarcaTorre({ tamanho, className = "" }: { tamanho: number; className?: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src="/icon.png"
      alt=""
      aria-hidden="true"
      width={tamanho}
      height={tamanho}
      className={`tc-marca shrink-0 object-cover ${className}`}
    />
  );
}
