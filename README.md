# Finanças do Erick — app pessoal

Python puro (sem dependências), SQLite, interface mobile. Telas: Mês, Semana (teto), Parcelas, Projeção, Ajustes.

## Rodar local
    APP_PASSWORD='uma-senha-forte' python3 server.py     # http://localhost:8080

## Docker
    cp .env.example .env     # edite APP_PASSWORD (mín. 8 caracteres) e, se quiser, BANCO_MCP_KEY
    docker compose up -d --build
Os dados ficam no volume `financas-data` (/data).

## Variáveis
APP_PASSWORD (obrigatória), PORT, HOST, DATA_DIR, BANCO_MCP_KEY (opcional, sincroniza gastos),
TZ_OFFSET_HOURS (padrão -3), COOKIE_SECURE (1 atrás de HTTPS), NO_SEED=1 (começa vazio).

## Hospedagem grátis (verifique os termos atuais nos sites)
- **Oracle Cloud Always Free** (VM Arm): melhor opção, disco persistente. Cadastro exige cartão para verificação.
  Instale Docker, copie esta pasta, `docker compose up -d`. Para HTTPS use `docker-compose.https.yml`
  com um domínio grátis (ex.: DuckDNS) em `DOMINIO`, ou um túnel (Cloudflare Tunnel).
- **Render grátis**: dorme após 15 min inativo e o disco provavelmente é efêmero → use **Ajustes → Exportar backup**
  com frequência e importe após reiniciar.
- Nunca exponha sem HTTPS: a senha trafega no login.

## Passo a passo pelo celular (Render grátis)
1. Crie uma conta em github.com e um repositório novo (github.com/new), privado.
2. Envie os arquivos desta pasta para o repositório (no app do GitHub ou em github.com > Add file > Upload files).
3. Entre em render.com com a conta do GitHub > New > Blueprint > escolha o repositório. O arquivo `render.yaml` já configura tudo.
4. Quando pedir `APP_PASSWORD`, digite uma senha forte (mín. 8 caracteres). `BANCO_MCP_KEY` é opcional.
5. Abra o endereço `https://financas-xxxx.onrender.com` que o Render mostrar e entre com a senha.
6. Em Ajustes > Exportar backup, guarde uma cópia sempre que mexer nos dados (o disco grátis pode apagar).

## Segurança
Senha obrigatória, cookie HttpOnly/SameSite=Strict, bloqueio após 5 erros/60s, CSP, textos escapados.
A chave do banco fica só no ambiente do servidor, nunca no navegador.

## Sincronização com o banco
Os gastos do banco que batem em data e valor com um lançamento manual são "adotados" (sem duplicar).
Foi testada só contra um servidor simulado, não contra a API real: confira os primeiros resultados.
Se não usar a sincronização, deixe BANCO_MCP_KEY vazio.
