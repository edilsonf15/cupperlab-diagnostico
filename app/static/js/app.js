// Rota la pregunta de la IA en el mini-diagnóstico del hero
(function () {
  var el = document.getElementById("hvizQ");
  if (!el) return;
  var qs = ['"¿Conocéis esta empresa?"', '"¿A qué se dedica esta marca?"',
    '"¿Qué empresas recomiendas para esto?"', '"¿Es fiable esta web?"'];
  var i = 0;
  setInterval(function () {
    el.style.opacity = "0";
    setTimeout(function () { i = (i + 1) % qs.length; el.textContent = qs[i]; el.style.opacity = "1"; }, 400);
  }, 3000);
})();

// Animación de entrada de las tarjetas "Qué descubrirás"
(function () {
  function reveal() {
    var cards = document.querySelectorAll(".rcard");
    if (!("IntersectionObserver" in window)) {
      cards.forEach(function (c) { c.classList.add("in"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { threshold: 0.15 });
    cards.forEach(function (c) { io.observe(c); });
  }
  if (document.readyState !== "loading") reveal();
  else document.addEventListener("DOMContentLoaded", reveal);
})();

(function () {
  "use strict";
  var form = document.getElementById("diagForm");
  var err = document.getElementById("err");
  var loading = document.getElementById("loading");
  var progFill = document.getElementById("progFill");
  var stepText = document.getElementById("stepText");
  var loadDom = document.getElementById("loadDom");
  var submitBtn = document.getElementById("submitBtn");
  var results = document.getElementById("results");

  var STEPS = [
    "Conectando con tu web…",
    "Comprobando conexión segura y velocidad…",
    "Leyendo el mapa del sitio…",
    "Buscando enlaces rotos (404)…",
    "Analizando el SEO on-page…",
    "Midiendo tu preparación para la IA (GEO)…",
    "Calculando tu salud digital…",
  ];

  function color(score) {
    if (score >= 70) return "#1f9d6b";
    if (score >= 55) return "#dfa019";
    if (score >= 40) return "#f46434";
    return "#d0402a";
  }
  function gradeColor(g) {
    return { A: "#1f9d6b", B: "#1f9d6b", C: "#dfa019", D: "#f46434", E: "#d0402a" }[g] || "#58626f";
  }

  var shown = 0, target = 0, animTimer = null;
  function setProgress(pct, stage) {
    target = pct;
    if (stage) stepText.textContent = stage;
    document.getElementById("progPct").textContent = Math.round(shown) + "%";
  }
  function startLoading(dom) {
    shown = 0; target = 3;
    loading.style.display = "block";
    loadDom.textContent = dom;
    progFill.style.width = "0%";
    document.getElementById("progPct").textContent = "0%";
    clearInterval(animTimer);
    animTimer = setInterval(function () {
      // avanza suave hacia el target real (nunca lo pasa)
      if (shown < target) shown = Math.min(shown + Math.max(0.4, (target - shown) * 0.12), target);
      progFill.style.width = shown + "%";
      document.getElementById("progPct").textContent = Math.round(shown) + "%";
    }, 60);
  }
  function stopLoading(done) {
    target = 100; if (done) shown = 100;
    setTimeout(function () { clearInterval(animTimer); progFill.style.width = "100%"; }, 200);
    setTimeout(function () { loading.style.display = "none"; }, 500);
  }
  function resetBtn(txt) { submitBtn.disabled = false; submitBtn.textContent = txt; }
  function fail(msg) {
    clearInterval(animTimer); loading.style.display = "none";
    err.textContent = msg; err.style.display = "block";
    resetBtn("Analizar mi web →");
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    err.style.display = "none";
    if (!document.getElementById("consent").checked) {
      err.textContent = "Marca la casilla para que podamos analizar tu web.";
      err.style.display = "block"; return;
    }
    var payload = {
      url: document.getElementById("url").value,
      email: document.getElementById("email").value,
      name: document.getElementById("name").value,
      phone: document.getElementById("phone").value,
    };
    submitBtn.disabled = true;
    submitBtn.textContent = "Analizando…";
    startLoading(payload.url);

    try {
      var resp = await fetch("/api/analyze", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      var start = await resp.json();
      if (!resp.ok) { fail(start.error || "Algo salió mal. Inténtalo de nuevo."); return; }
      var jobId = start.job_id;
      if (!jobId) { fail("Recarga la página (Ctrl+F5) e inténtalo de nuevo: el servidor necesita reiniciarse."); return; }

      // Polling del progreso real (robusto)
      var f404 = 0, fFetch = 0;
      for (var i = 0; i < 240; i++) {
        await sleep(1500);
        var s, sr;
        try {
          sr = await fetch("/api/status/" + jobId, { cache: "no-store" });
        } catch (e2) {
          if (++fFetch >= 10) { fail("Perdimos la conexión con el servidor. Revisa tu conexión e inténtalo de nuevo."); return; }
          continue;
        }
        fFetch = 0;
        if (sr.status === 404) {
          if (++f404 >= 3) { fail("No pudimos seguir el análisis. Reinicia el servidor (Ctrl+C y vuelve a arrancarlo) y recarga la página."); return; }
          continue;
        }
        try { s = await sr.json(); } catch (e3) { continue; }
        if (s.error) { fail(s.error); return; }
        setProgress(s.progress || 0, s.stage);
        if (s.done && s.result) {
          stopLoading(true);
          await sleep(450);
          render({ result: s.result, contact: s.contact });
          return;
        }
      }
      fail("El análisis está tardando más de lo normal. Te lo enviamos por correo en cuanto termine.");
    } catch (ex) {
      fail("No pudimos completar el análisis. Revisa la conexión e inténtalo de nuevo.");
    }
  });

  function render(data) {
    var r = data.result;
    results.classList.add("on");
    submitBtn.disabled = false;
    submitBtn.textContent = "Analizar otra web →";

    // Gauge
    var arc = document.getElementById("gaugeArc");
    var len = (r.score / 100) * 257.6;
    arc.setAttribute("stroke", color(r.score));
    // animación
    var cur = 0;
    var anim = setInterval(function () {
      cur += Math.max(1, Math.round(r.score / 30));
      if (cur >= r.score) { cur = r.score; clearInterval(anim); }
      document.getElementById("scoreNum").innerHTML = cur + "<span>/100</span>";
      arc.setAttribute("stroke-dasharray", ((cur / 100) * 257.6).toFixed(1) + " 257.6");
    }, 22);

    var pill = document.getElementById("gradePill");
    pill.textContent = "Nota " + r.grade;
    pill.style.background = gradeColor(r.grade);

    var verdict = r.score >= 70 ? "Vas bien, con margen de mejora."
      : r.score >= 55 ? "Estás a medio camino: hay palancas claras que activar."
      : r.score >= 40 ? "Tienes puntos importantes que corregir para que te encuentren."
      : "Ahora mismo Google y la IA apenas te ven. Hay mucho por ganar.";
    document.getElementById("resTitle").textContent = verdict;
    document.getElementById("resDom").textContent = r.domain;
    document.getElementById("resSummary").textContent =
      "Analizamos " + r.domain + " en vivo: SEO, cómo te ve la IA, velocidad y competencia. Esto mismo, con el plan de acción completo, te llega en PDF al correo.";

    // Categorías
    var cats = document.getElementById("cats");
    cats.innerHTML = "";
    [["tecnico"], ["onpage"], ["geo"]].forEach(function (k) {
      var c = r.categories[k[0]];
      if (!c) return;
      var el = document.createElement("div");
      el.className = "catcard";
      el.innerHTML =
        '<div class="lab"><span>' + c.label + '</span><b>' + c.score + '</b></div>' +
        '<div class="track"><i style="width:' + c.score + '%;background:' + color(c.score) + '"></i></div>';
      cats.appendChild(el);
    });

    // ---- Franja: cómo te ve la IA (real, general) ----
    function esc(t) { return (t || "").toString().replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
    var ai = r.geo_ai;
    var aihi = document.getElementById("aihi");
    var geoScore = (r.categories.geo || {}).score || 0;
    if (ai && ai.available && !ai.error && (ai.answered_names || []).length) {
      var k1 = ai.knows_brand, r1 = ai.recommended;
      var comp = (ai.competitors || []).slice(0, 3).map(function (c) { return c && c.name ? c.name : c; }).join(", ");
      aihi.innerHTML =
        '<div class="ai-k">◆ Cómo te ve la IA</div>' +
        '<div class="ai-row">' +
          '<div class="ai-score"><b>' + (ai.ai_score || 0) + '</b><span>/100 en IA</span></div>' +
          '<div class="ai-facts">' +
            '<div class="ai-f ' + (k1 ? "y" : "n") + '"><i>' + (k1 ? "✓" : "✕") + '</i> ¿La IA sabe quién eres? <b>' + (k1 ? "Sí, te describe" : "No") + '</b></div>' +
            '<div class="ai-f ' + (r1 ? "y" : "n") + '"><i>' + (r1 ? "✓" : "✕") + '</i> ¿Te recomienda al pedir tu servicio? <b>' + (r1 ? "Sí" : (r1 === false ? "No, nombra a " + esc(comp) : "—")) + '</b></div>' +
          '</div>' +
        '</div>' +
        '<p class="ai-note">Le preguntamos a la IA por tu marca y tu servicio, en vivo. Las respuestas textuales van en el informe.</p>';
      aihi.style.display = "block";
    } else {
      aihi.innerHTML =
        '<div class="ai-k">◆ Cómo te ve la IA</div>' +
        '<div class="ai-row"><div class="ai-score"><b>' + geoScore + '</b><span>/100 GEO</span></div>' +
        '<div class="ai-facts"><div style="color:#dfe4ea;font-size:13px">Medimos tu preparación para los buscadores con IA. El detalle de las pruebas va en el informe.</div></div></div>';
      aihi.style.display = "block";
    }

    // ---- Velocidad móvil/escritorio ----
    var velo = document.getElementById("velo");
    var psi = r.psi_full;
    if (psi && (psi.mobile || psi.desktop)) {
      function speedCard(title, d) {
        if (!d || d.performance == null) return '<div class="vcard"><div class="vt">' + title + '</div><div class="vmut">Medición no disponible.</div></div>';
        var p = d.performance, col = color(p);
        var mets = [["FCP", d.fcp], ["LCP", d.lcp], ["TBT", d.tbt]].map(function (m) {
          return '<div class="vmet"><span>' + m[0] + '</span><b>' + (m[1] || "-") + '</b></div>';
        }).join("");
        return '<div class="vcard"><div class="vhead"><span class="vt">' + title + '</span>' +
          '<span class="vscore" style="color:' + col + '">' + p + '<i>/100</i></span></div>' +
          '<div class="vring" style="--p:' + p + ';--c:' + col + '"></div>' +
          '<div class="vmets">' + mets + '</div></div>';
      }
      velo.innerHTML = '<div class="ai-k" style="color:#0f9bc2">◆ Velocidad (Google PageSpeed)</div>' +
        '<div class="vgrid">' + speedCard("📱 Móvil", psi.mobile) + speedCard("💻 Escritorio", psi.desktop) + '</div>';
      velo.style.display = "block";
    } else { velo.style.display = "none"; }

    // ---- Competencia ----
    var compe = document.getElementById("compe");
    var comps = (ai && ai.competitors) ? ai.competitors.slice(0, 5) : [];
    if (comps.length) {
      compe.innerHTML = '<div class="ai-k" style="color:#0f9bc2">◆ Tu competencia real según la IA</div>' +
        '<p class="cp-sub">Empresas de tu mismo sector que la IA recomienda cuando alguien pide tu servicio y tu marca no aparece:</p>' +
        '<div class="cp-list">' + comps.map(function (c) {
          var nm = (c && c.name) ? c.name : c;
          var dm = (c && c.domain) ? '<span class="cp-dom">' + esc(c.domain) + '</span>' : "";
          return '<span class="cp-chip">' + esc(nm) + dm + '</span>';
        }).join("") + '</div>';
      compe.style.display = "block";
    } else { compe.style.display = "none"; }

    // Aviso de correo
    var mailed = document.getElementById("mailed");
    mailed.className = "mailed";
    mailed.innerHTML = "📬 Te enviamos a tu correo un <b>análisis aún más completo</b>, con el plan de acción en PDF. Revisa también spam.";

    results.scrollIntoView({ behavior: "smooth" });
  }
})();
