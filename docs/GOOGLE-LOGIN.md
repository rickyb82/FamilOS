# Login Google per chi installa FamilOS

Il login usa Google OpenID Connect con Authorization Code + PKCE e i soli scope openid, email e profile.
Non limita gli account al dominio Gmail: può usare anche un account Google con indirizzo esterno o Workspace, se consentito dalle policy dell'amministratore Workspace.

## Configurazione

1. Scegliere un dominio HTTPS dell'installazione, per esempio https://casa.example.it, in **Installazione → Dominio e passkey**.
2. In **HTTPS e certificati**, configurare Let’s Encrypt o il certificato appropriato; applicare sul server con `./familos.sh domain apply`. DNS e challenge devono raggiungere il server.
3. Nella [Google Cloud Console](https://console.cloud.google.com/), creare un progetto e configurare **Google Auth Platform → Branding** con nome, contatto e informazioni richieste.
4. In **Audience**, scegliere **External / Esterno** per ammettere account Google esterni alla propria organizzazione. Pubblicare **In production / In produzione** per l'uso stabile; completare le verifiche richieste da Google per il proprio caso.
5. In **Clients**, creare un client OAuth **Web application / Applicazione web**. Inserire l'origine e l'URI esatti che FamilOS mostra in **Installazione → Accesso con Google**.
6. Incollare Client ID e Client secret in FamilOS e salvare. Il secret viene cifrato sul server.
7. Creare o invitare nella famiglia un membro adulto con la stessa email dell'account Google, quindi provare “Continua con Google”.

Esempio:

- Origine JavaScript: `https://casa.example.it`
- Redirect del login: `https://casa.example.it/api/auth/google/callback`
- Redirect Gmail opzionale, separato dal login: `https://casa.example.it/api/admin/email/google/callback`

## Tutti gli account, con accesso autorizzato

External + In production permette l'autorizzazione degli account Google esterni. Alcuni account gestiti, supervisionati o protetti possono avere ulteriori restrizioni imposte da Google o dall'organizzazione.
FamilOS ammette soltanto membri adulti già attivi e autorizzati: **non crea utenti automaticamente** al primo login. I bambini continuano ad avere il loro accesso protetto.
Per invitare un altro familiare basta aggiungere il suo indirizzo Google nella famiglia; non occorre renderlo amministratore o aggiungerlo al progetto Cloud.

## Distribuzione del framework su domini diversi

Ogni installazione può creare il proprio client OAuth e inserire il proprio callback. Uno stesso client può avere più redirect registrati esplicitamente, ma Google non accetta un redirect jolly per domini arbitrari.
Il Client secret resta sul server; non va incluso nel repository distribuito. Il logo locale di FamilOS non modifica automaticamente il branding del consenso Google: si configura separatamente in Google Auth Platform.

## Errori frequenti

- **redirect_uri_mismatch**: confrontare schema, dominio, porta e percorso con il callback mostrato nel pannello, senza slash finale aggiunto.
- **org_internal**: il progetto è Internal; scegliere External se deve accettare account esterni.
- **google_utente_non_registrato**: l'email Google non corrisponde a un membro adulto attivo. Aggiungerlo nella famiglia.
- Il logo/nome non compare nel consenso: controllare il branding e l'eventuale verifica Google.
- Account Workspace rifiutato: far controllare all'amministratore le policy sulle app OAuth.

Riferimenti ufficiali: [Audience](https://support.google.com/cloud/answer/15549945), [stati OAuth](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview), [redirect OAuth](https://developers.google.com/identity/protocols/oauth2/web-server).
