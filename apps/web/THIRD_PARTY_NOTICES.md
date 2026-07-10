# Avisos de terceiros — apps/web

## Mapa do Brasil por UF (`src/lib/brazil-uf-paths.ts`)

- **Fonte:** pacote npm [`@svg-maps/brazil`](https://www.npmjs.com/package/@svg-maps/brazil), versão 2.0.0
- **Projeto:** [VictorCazanave/svg-maps](https://github.com/VictorCazanave/svg-maps)
- **Autor:** Victor Cazanave
- **Licença:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)

Os dados de geometria (`id`/`name`/`path` por UF) foram extraídos uma única
vez do pacote publicado e copiados para `src/lib/brazil-uf-paths.ts` como
asset local versionado. Nenhuma chamada externa/CDN acontece em runtime — o
mapa é renderizado inteiramente a partir deste arquivo local.

A atribuição também aparece:
- no cabeçalho de `src/lib/brazil-uf-paths.ts`;
- em texto discreto no rodapé do card "Mapa regional" (`RegioesBrazilMap.tsx`), em `/regioes`.
