/* Un chiffre qui se compte doit garder sa forme.

   C'est tout l'enjeu du décompte : une mesure n'est pas un nombre, c'est une
   chaîne — « 46 % », « 4.2 Mo/s », « 24 318 ». Compter la valeur est facile ;
   la réécrire à l'identique, moins. Un espace de milliers avalé, une décimale
   perdue, une unité collée au chiffre, et le mouvement se lit comme un défaut
   d'affichage plutôt que comme une mesure qui bouge.

   Les images sont simulées : « requestAnimationFrame » dépose ses fonctions
   dans une file qu'on avance à la main, en les datant depuis l'instant du
   départ. Le décompte devient reproductible, ce qu'aucune capture ne serait.
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
const dit = (bon, titre, detail) =>
  console.log((bon ? '  ok    ' : '  ECHEC ') + titre + (detail ? '   ' + detail : ''));

setTimeout(() => {
  inj(`
    animations = true;
    document.documentElement.setAttribute('data-anim', '1');
    /* Les images passent sous notre contrôle. L'horloge, elle, reste celle du
       navigateur : « performance » n'est pas remplaçable partout, et un
       décompte qui lirait deux horloges différentes ne finirait jamais. On
       relève donc l'instant du départ, et on date les images à partir de lui. */
    window.__files = [];
    window.requestAnimationFrame = function (f) { window.__files.push(f); return window.__files.length; };

    /* Déroule un décompte entier et rend toutes les valeurs écrites. */
    window.__jouer = function (depuis, vers, pas) {
      var el = { textContent: depuis };
      window.__files.length = 0;
      var t0 = performance.now();
      poserMesure(el, vers);
      var vues = [el.textContent], n = pas || 12;
      for (var i = 1; i <= n && window.__files.length; i++) {
        window.__files.shift()(t0 + (650 / n) * i);
        vues.push(el.textContent);
      }
      /* Une garde : si le décompte ne se terminait pas, la file grossirait
         sans fin — c'est exactement ce qu'on veut voir échouer, pas planter. */
      var garde = 0;
      while (window.__files.length && garde++ < 40) {
        window.__files.shift()(t0 + 700); vues.push(el.textContent);
      }
      return { fin: el.textContent, vues: vues, fuite: window.__files.length > 0 };
    };
  `);

  const jouer = (a, b) => {
    inj('window.__r = window.__jouer(' + JSON.stringify(a) + ',' + JSON.stringify(b) + ');');
    return w.__r;
  };

  const FIN = ' ', NBSP = ' ';   // insécable étroite, insécable

  console.log('=== la valeur d\'arrivée est exacte ===');
  [['5', '68 %'], ['11 %', '46 %'], ['4.2 Mo/s', '8.7 Mo/s'],
   ['24 318', '24 500'], ['1' + FIN + '204', '1' + FIN + '780'],
   ['2', '3'], ['128', '41']].forEach((c) => {
    const r = jouer(c[0], c[1]);
    dit(r.fin === c[1], JSON.stringify(c[0]) + ' → ' + JSON.stringify(c[1]),
        JSON.stringify(r.fin));
  });

  console.log('\n=== l\'unité ne bouge pas pendant le décompte ===');
  {
    const r = jouer('11 %', '46 %');
    const mauvaises = r.vues.filter((v) => !/^\d+ %$/.test(v));
    dit(mauvaises.length === 0, 'chaque pas reste « n % »',
        mauvaises.length ? JSON.stringify(mauvaises.slice(0, 3))
                         : r.vues.slice(0, 4).join(' · '));
  }
  {
    /* Le défaut d'origine : la classe de caractères tenait l'espace pour un
       séparateur de milliers, l'avalait, et « 68 % » revenait « 68% ». */
    const r = jouer('5', '68 %');
    const colles = r.vues.filter((v) => /\d%/.test(v));
    dit(colles.length === 0, 'l\'espace avant le % n\'est jamais avalé',
        colles.length ? JSON.stringify(colles.slice(0, 3)) : 'aucun');
  }

  console.log('\n=== les décimales suivent la valeur d\'arrivée ===');
  {
    const r = jouer('4.2 Mo/s', '8.7 Mo/s');
    const mauvaises = r.vues.filter((v) => !/^\d+\.\d Mo\/s$/.test(v));
    dit(mauvaises.length === 0, 'une décimale, toujours une',
        mauvaises.length ? JSON.stringify(mauvaises.slice(0, 3))
                         : r.vues.slice(0, 4).join(' · '));
  }

  console.log('\n=== le séparateur des milliers est celui qu\'on a reçu ===');
  {
    const r = jouer('24 318', '24 500');
    dit(r.vues.every((v) => /^\d{2} \d{3}$/.test(v)), 'espace ordinaire conservé',
        r.vues.slice(0, 3).join(' · '));
  }
  {
    const r = jouer('1' + FIN + '204', '1' + FIN + '780');
    dit(r.vues.every((v) => v.indexOf(FIN) > 0), 'insécable étroite conservée',
        JSON.stringify(r.vues.slice(0, 3)));
  }
  {
    const r = jouer('12' + NBSP + '000', '15' + NBSP + '400');
    dit(r.fin === '15' + NBSP + '400', 'insécable conservée', JSON.stringify(r.fin));
  }

  console.log('\n=== ce qui n\'est pas un nombre s\'écrit sèchement ===');
  inj(`
    var el = { textContent: 'hors ligne' };
    window.__files.length = 0; poserMesure(el, 'en ligne');
    window.__texte = el.textContent + '/' + window.__files.length;
    var e2 = { textContent: '46 %' };
    window.__files.length = 0; poserMesure(e2, '46 %');
    window.__inchange = window.__files.length;
  `);
  dit(w.__texte === 'en ligne/0', 'aucun décompte sur du texte', w.__texte);
  dit(w.__inchange === 0, 'une valeur inchangée ne déclenche rien');

  console.log('\n=== quand le calme est demandé, rien ne bouge ===');
  inj(`
    animations = false;
    var el = { textContent: '5' };
    window.__files.length = 0; poserMesure(el, '68 %');
    window.__calme = el.textContent + '/' + window.__files.length;
    animations = true;
  `);
  dit(w.__calme === '68 %/0', 'la valeur est posée d\'un coup', w.__calme);

  console.log('\n=== deux décomptes sur le même chiffre : le dernier gagne ===');
  inj(`
    var el = { textContent: '10' };
    window.__files.length = 0;
    var t0 = performance.now();
    poserMesure(el, '90');                 // premier départ
    var premier = window.__files.shift();
    poserMesure(el, '20');                 // la mesure change en cours de route
    window.__files.shift()(t0 + 300);
    var apres = el.textContent;
    premier(t0 + 320);                     // l'ancienne boucle revient
    window.__jeton = (el.textContent === apres) + '/' + el.textContent;
  `);
  dit(String(w.__jeton).indexOf('true') === 0, 'la boucle abandonnée n\'écrit plus',
      String(w.__jeton));

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);
