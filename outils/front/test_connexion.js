/* L'écran de connexion : qui l'on désigne, et qui l'on devient.

   Quatre défauts sont vérifiés ici, tous vus à l'usage :

     — choisir un visage après « Autre profil… » gardait le champ « nom » et
       redemandait ce qu'on venait de désigner ;
     — sans profil choisi, le formulaire connectait silencieusement le premier
       de la liste, c'est-à-dire quelqu'un d'autre que celui dont on tapait le
       mot de passe ;
     — « Se connecter avec ce profil », dans les réglages, se contentait
       d'écrire un nom dans une variable locale : l'en-tête changeait, la
       session non ;
     — on ne pouvait pas relire son mot de passe.
*/
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const cible = process.argv[2] || path.join(__dirname, '..', '..', 'app', 'static', 'index.html');
const dom = new JSDOM(fs.readFileSync(cible, 'utf8'), {
  runScripts: 'dangerously', resources: 'usable',
  url: 'file:///' + cible, pretendToBeVisual: true });
const w = dom.window;
const ERREURS = []; w.addEventListener('error', (e) => ERREURS.push(String(e.message)));
w.WebSocket = function () { this.close = () => {}; this.send = () => {}; };
w.fetch = () => Promise.reject(new Error('hors ligne'));
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const $ = (id) => w.document.getElementById(id);
const attends = (ms) => new Promise((r) => setTimeout(r, ms));
const dit = (bon, titre, detail) =>
  console.log((bon ? '  ok    ' : '  ECHEC ') + titre + (detail ? '   ' + detail : ''));

setTimeout(async () => {
  inj(`
    /* Deux profils : un protégé, un libre — la maison type. */
    USERS = [{ nom: 'Guillaume', pwd: '1', photo: '' }, { nom: 'Invité', pwd: '', photo: '' }];
    PROFILS = [{ nom: 'Guillaume', protege: true, photo: '' },
               { nom: 'Invité', protege: false, photo: '' }];
    SESSION_USER = null; ACTIVE = '';
    window.__envois = [];
    relayFetch = function (chemin, opts) {
      if (chemin.indexOf('/api/login') === 0) {
        var corps = JSON.parse((opts && opts.body) || '{}');
        window.__envois.push(corps.nom);
        return Promise.resolve({ ok: true, json: function () {
          return Promise.resolve({ utilisateur: corps.nom }); } });
      }
      return Promise.reject(new Error('hors ligne'));
    };
    i18n.langue = 'fr'; i18n.appliquer(document);
    $('lock').hidden = false; renderLockUi();
  `);
  await attends(200);

  const visages = () => [...w.document.querySelectorAll('#lock-profiles .lock-p')];
  console.log('=== « Autre profil… » puis clic sur un visage ===');
  $('lock-autre').click();
  await attends(60);
  dit($('lock-nom-bloc').hidden === false, 'la saisie libre ouvre le champ « nom »');
  visages()[0].click();
  await attends(60);
  dit($('lock-nom-bloc').hidden === true, 'choisir un visage referme ce champ');
  dit($('lock-lab').textContent.includes('Guillaume'),
      "et le formulaire ne demande plus que le mot de passe", $('lock-lab').textContent);
  $('lock-input').value = 'motdepasse';
  $('lock-go').click();
  await attends(120);
  dit(w.__envois.join() === 'Guillaume', "le nom envoyé est celui qu'on a désigné",
      JSON.stringify(w.__envois));

  console.log("\n=== sans profil désigné, rien n'est deviné ===");
  inj(`SESSION_USER = null; window.__envois = []; PENDING_USER = -1;
       AUTRE_PROFIL = false; $('lock-form').hidden = false; $('lock-input').value = 'x';`);
  $('lock-go').click();
  await attends(120);
  dit(w.__envois.length === 0, "aucune connexion n'est tentée", JSON.stringify(w.__envois));
  dit(($('lock-err').textContent || '').length > 0, "et l'écran dit pourquoi",
      $('lock-err').textContent);

  console.log("\n=== l'identité affichée suit la session, pas un souvenir local ===");
  inj(`SESSION_USER = 'Invité'; ACTIVE = 'Guillaume'; window.__u = userActif().nom;`);
  dit(w.__u === 'Invité', 'la session nomme', w.__u);
  inj(`SESSION_USER = null; ACTIVE = 'Guillaume'; window.__u = userActif().nom;`);
  dit(w.__u === 'Guillaume', "hors session, le dernier profil connu sert d'indice", w.__u);

  console.log('\n=== changer de compte ferme la session en cours ===');
  inj(`SESSION_USER = 'Guillaume'; ACTIVE = 'Guillaume';
       SEL_USER = 1; window.__deconnecte = false;
       relayFetch = function (chemin) {
         if (chemin.indexOf('/api/logout') === 0) { window.__deconnecte = true;
           return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } }); }
         return Promise.reject(new Error('hors ligne'));
       };
       $('spu-activate').click();`);
  await attends(250);
  inj('window.__session = SESSION_USER; window.__vise = PROFIL_VISE;');
  dit(w.__deconnecte === true, 'la session serveur est fermée');
  dit($('lock').hidden === false, "l'écran de connexion revient");
  dit(w.__session === null, "et personne n'est connecté entre-temps");

  console.log('\n=== relire son mot de passe ===');
  inj(`$('lock-input').type = 'password';`);
  $('lock-eye').click();
  dit($('lock-input').type === 'text', "l'œil dévoile la saisie");
  $('lock-eye').click();
  dit($('lock-input').type === 'password', 'et la recache');

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);
