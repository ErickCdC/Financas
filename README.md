# Finanças

App pessoal, instalável no Chrome (PWA), dividido em módulos. A troca de módulo fica no topo; cada módulo tem suas abas embaixo. Ajustes ficam na engrenagem.

- **Hoje:** resumo de tudo — quanto ainda dá para gastar na semana, próxima conta, treinos da semana com check-in de um toque, hábitos do dia, próximos 7 dias e tarefas de hoje.
- **Finanças:** Início, Contas, Gastos e Planejamento (projeção com simulador de teto + parcelas), ligado aos bancos.
- **Saúde:** Treino (check-in de um toque, meta semanal, sequência de semanas, mapa dos últimos 4 meses), Corpo (peso, altura, IMC, bíceps, coxa e outras medidas com gráfico; bioimpedância com o laudo em foto ou PDF) e Hábitos (água, sono e os que você criar, com sequência de dias).
- **Agenda:** Próximos (tudo que tem data, inclusive contas a vencer), Notas por pasta (Trabalho, Pessoal, Faculdade…) com lembrete que avisa 1, 2, 3 ou 7 dias antes e perto da hora, Tarefas com prazo e Faculdade (provas e trabalhos dos dois cursos, com contagem regressiva e médias).
- **Arquivos:** fotos são reduzidas no aparelho (1600 px); fotos e PDFs vão para o GitHub em `arquivos/` no branch de backup, separados do JSON.
- **Notificações:** lembrete diário de gastos, alerta do teto (80%/100%), contas a vencer, resumo semanal, dia do salário e sincronização diária com o banco (opcional). Têm horário de silêncio e cada uma pode ser ligada, desligada e ter o horário trocado.
- **Livre até o salário:** quanto dá para gastar até o próximo salário sem ficar no vermelho, por dia, e onde você termina no ritmo atual.
- **Teto automático (opcional):** defina quanto quer guardar por mês e o teto semanal se ajusta sozinho.
- **Metas em potes separados:** reserva de emergência e casa própria (MCMV), com previsão de quando cada uma chega.
- **Avulsos:** despesas e entradas que não se repetem (caução, presentes, horas extras), nas contas do mês e na projeção.
- **Calendário** de vencimentos, salário e gastos por dia; **Raio-X** com ritmo, comparação com o salário anterior, transporte, assinaturas e juros de parcelamentos.
- **Lançamento rápido** com as descrições mais frequentes e a categoria aprendida; botão "Lançar gasto" direto na notificação.
- **Funciona no celular e no computador**, com tema claro/escuro e abertura rápida mesmo com o servidor dormindo.

## Configuração no Render (uma vez)

O `render.yaml` já cria `APP_SECRET` e `CRON_TOKEN` sozinho. Confira em **financas › Environment**. Se não aparecerem, crie os dois com um texto longo e aleatório.

### 1. Backup automático (obrigatório no plano grátis)

O disco do Render grátis apaga quando o servidor reinicia. O app salva tudo no branch `dados` deste repositório e restaura de lá ao iniciar.

1. Abra https://github.com/settings/personal-access-tokens/new
2. **Repository access:** *Only select repositories* › `Financas`.
3. **Permissions › Repository permissions › Contents:** *Read and write*.
4. Gere o token e cole no Render como `GITHUB_TOKEN`. Salve.

Em **Ajustes › Dados** o app mostra "Backup automático: ativo".

### 2. Agendador das notificações

O Render grátis dorme depois de 15 minutos. O GitHub Actions acorda o app a cada 30 minutos e roda a rotina (`.github/workflows/lembretes.yml`).

1. No repositório: **Settings › Secrets and variables › Actions › New repository secret**.
2. `APP_URL` = `https://financas-153m.onrender.com`
3. `CRON_TOKEN` = o mesmo valor de `CRON_TOKEN` do Render (botão do olho para ver).
4. Teste em **Actions › Lembretes › Run workflow**.

O GitHub às vezes atrasa execuções agendadas em 10 a 30 minutos, e pausa agendamentos de repositórios sem atividade por 60 dias.

### 3. No celular

1. Abra o app no Chrome › menu ⋮ › **Instalar app**.
2. Abra pelo ícone › **Ajustes › Notificações › Ativar** › **Testar**.

## Sincronização e automação

Com `BANCO_MCP_KEY`, o app lê o banco a cada 30 minutos das 7:30 às 23:00 (dá para ajustar em **Ajustes › Banco**). Em cada leitura ele:

- lança os gastos do cartão e os Pix pequenos, sem duplicar os lançados à mão;
- junta compra e estorno do mesmo valor e tira os dois do teto ("estornado");
- detecta o salário que caiu (crédito de pelo menos 60% do salário perto do dia do salário) e usa o valor real;
- marca como pagas as faturas (pelo "Pagamento recebido" do cartão) e as contas fixas (pelas palavras em "Como aparece no extrato");
- usa o valor real das faturas, fechadas e a aberta, no lugar da previsão (valores que você informa nunca são trocados);
- calcula o caixa: saldo das contas menos as contas do mês ainda não pagas;
- avança as parcelas quando a fatura é paga ou vence.

Limites do provedor: cerca de 2 requisições por segundo, e pedir dados novos ao banco ("forçar atualização") no máximo 1 vez por hora por banco. O app respeita os dois.

## Variáveis de ambiente

| Variável | Para quê |
|---|---|
| `APP_PASSWORD` | Senha de entrada (mín. 8 caracteres). Obrigatória. |
| `APP_SECRET` | Chave interna (sessões e chave das notificações). Trocar desativa as notificações já ativadas. |
| `CRON_TOKEN` | Senha do agendador (`POST /api/cron`). |
| `GITHUB_TOKEN`, `GITHUB_REPO`, `GITHUB_BRANCH` | Backup automático. |
| `BANCO_MCP_KEY` | Opcional: sincronização com o Banco MCP. |
| `TZ_OFFSET_HOURS` | Fuso (padrão `-3`). |

## Rodar no computador

    pip install -r requirements.txt
    APP_PASSWORD='uma-senha-forte' python3 server.py     # http://localhost:8080

Ou com Docker: `cp .env.example .env`, preencha e rode `docker compose up -d --build`. Notificações exigem HTTPS (no computador só funcionam em `localhost`).

## Estrutura

    server.py            ponto de entrada
    app/config.py        variáveis de ambiente
    app/db.py            SQLite e migrações
    app/finance.py       painel do mês, teto, vencimentos, resumo
    app/projection.py    previsão de faturas e projeção
    app/notify.py        regras e envio das notificações
    app/push.py          Web Push (criptografia RFC 8291 + VAPID)
    app/backup.py        backup no GitHub
    app/bank.py          sincronização com o Banco MCP
    app/vida.py          Saúde e Agenda: treinos, medidas, hábitos, notas, tarefas, faculdade, arquivos e avisos
    app/web.py           HTTP, login e segurança
    static/              interface (PWA, service worker, ícones); modulos.js tem Hoje, Saúde e Agenda
    tests/               testes (`python3 -m unittest discover -s tests`)

## Segurança

Senha obrigatória com bloqueio após tentativas erradas, cookie `HttpOnly`/`SameSite=Strict`/`Secure`, CSP sem scripts ou estilos inline, textos sempre escapados, notificações só para serviços de push conhecidos. As chaves ficam só nas variáveis do servidor. O backup fica no seu repositório privado: mantenha-o privado.
