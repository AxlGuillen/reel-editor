"""Tests de modules/competitivo/kills.py (sin internet).

    venv\\Scripts\\python -m unittest discover -s tests -t .

El fixture es el timeline real de KC vs C9 G1 (15/10), compactado a un frame
cada ~9.5 s. Las peleas sintéticas cubren lo que el fixture no trae
(kills por jugador, remontadas, intercambios, fusión de ventanas).
"""
import os
import unittest
from datetime import datetime, timedelta, timezone

from modules.competitivo import engine
from modules.competitivo import kills as K

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json")


class FixtureKCvsC9(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.partida = K.partida_de_fixture(FIXTURE)
        cls.peleas = K.detectar_peleas(cls.partida)
        # Presupuesto fijo de 60 s (el de la primera versión): da los dos clips
        # de siempre, que usan varios tests.
        cls.reglas_60 = K.Reglas(duracion_max_s=60, duracion_min_s=0, duracion_auto=False, max_por_partida=3)
        cls.clips = K.seleccionar([K.detectar_peleas(cls.partida, cls.reglas_60)], cls.reglas_60)

    def test_con_los_defaults_dura_minutos_por_4(self):
        # La partida dura 34.9 min → objetivo 139 s (entre 90 y 180).
        self.assertAlmostEqual(K.minutos_de(self.partida), 34.86, delta=0.05)
        self.assertEqual(K.objetivo_s(K.Reglas(), K.minutos_de(self.partida)), 139.4)
        clips = K.seleccionar([K.detectar_peleas(self.partida)])
        self.assertEqual([c.reloj for c in clips], ["20:30", "24:30", "25:40", "34:50"])
        total = sum(c.duracion for c in clips)
        self.assertLessEqual(total, 139.4)
        self.assertGreaterEqual(total, 90)
        self.assertFalse(any(c.relleno for c in clips))

    def test_dos_clips_con_60_s(self):
        # Orden cronológico, no de puntaje.
        self.assertEqual([c.titulo for c in self.clips],
                         ["C9 · pelea 2-0 → barón · 25:40", "C9 · pelea 3-0 → nexo · 34:50"])
        total = sum(c.duracion for c in self.clips)
        self.assertLessEqual(total, 60)
        # La pelea final tiene kills en DOS frames (34:40 y 34:50), así que con
        # la ventana de 10 s + 5 s su clip dura ~35 s: 25 + 35 ≈ 60.
        self.assertAlmostEqual(total, 60, delta=1.5)

    def test_puntajes_del_brief(self):
        baron, cierre = self.clips
        # 60 + 15 + 30 + 14 (+ oro) → tope 100
        d = cierre.desglose
        self.assertEqual((d["kills"], d["ganador_3"], d["cierre"], d["minuto"]), (60, 15, 30, 14))
        self.assertEqual(cierre.puntaje, 100)
        self.assertTrue(cierre.cierra and cierre.forzada)
        # 40 + 5 + 10 = 55, más el bonus de oro (pedido aparte del brief)
        d = baron.desglose
        self.assertEqual((d["kills"], d["minuto"], d["objetivo"]), (40, 5, 10))
        self.assertEqual(baron.puntaje - d["oro"], 55)
        self.assertGreater(d["oro"], 0)

    def test_ventana_del_clip(self):
        baron, cierre = self.clips
        # La kill pasó entre el frame anterior y el que sube el contador:
        # inicio = frame anterior − pre_s (10); fin = última kill + post_s (5).
        self.assertEqual((K.Reglas().pre_s, K.Reglas().post_s), (10, 5))
        for c in self.clips:
            self.assertAlmostEqual((c.antes - c.clip_inicio).total_seconds(), 10, places=3)
            self.assertAlmostEqual((c.clip_fin - c.ultima).total_seconds(), 5, places=3)
            self.assertAlmostEqual((c.primera - c.antes).total_seconds(), 9.75, delta=0.75)
        self.assertAlmostEqual(baron.duracion, 25, delta=1)
        self.assertAlmostEqual(cierre.duracion, 35, delta=1)
        # con otra ventana, el clip cambia lo mismo
        reglas = K.Reglas(pre_s=8, post_s=3)
        otro = [p for p in K.detectar_peleas(self.partida, reglas) if p.reloj == "25:40"][0]
        self.assertAlmostEqual(otro.duracion, baron.duracion - 4, places=3)

    def test_cuenta_de_kills_igual_al_motor(self):
        # 15 eventos de kills: la primera sangre, 9 picks y 5 peleas de 2+.
        del_motor = [e for e in self.partida.eventos if e.tipo in ("kill", "pelea", "primera_sangre")]
        self.assertEqual(len(self.peleas), len(del_motor), 15)
        self.assertEqual(sum(1 for p in self.peleas if p.total == 1), 10)
        self.assertEqual(sum(1 for p in self.peleas if p.total >= 2), 5)
        self.assertEqual(sum(p.total for p in self.peleas), 21)   # 1 + 20

    def test_objetivos_nunca_generan_clip(self):
        # Todo candidato nace de kills; los objetivos sueltos (dragones de
        # 8:00 y 13:10, barón de 33:20) no caen dentro de ningún clip elegible.
        t0 = self.partida.t0
        for p in self.peleas:
            self.assertGreaterEqual(p.total, 1)
        for c in self.clips:
            self.assertGreaterEqual(c.total, 2)
        sueltos = [e for e in self.partida.eventos
                   if e.tipo in ("dragon", "baron") and e.reloj in ("8:00", "13:10", "33:20")]
        self.assertEqual(len(sueltos), 3)
        for e in sueltos:
            for p in self.peleas:
                if p.elegible:
                    self.assertFalse(p.clip_inicio <= e.ts <= p.clip_fin,
                                     f"{e.reloj} {e.tipo} dentro de {p.titulo} ({t0})")

    def test_un_tercero_no_cabe(self):
        reglas = K.Reglas(max_por_partida=5, duracion_max_s=60, duracion_min_s=0, duracion_auto=False)
        clips = K.seleccionar([K.detectar_peleas(self.partida, reglas)], reglas)
        self.assertEqual(len(clips), 2)   # el siguiente (24:30, ~21 s) pasaría de 60

    def test_picks_y_presupuesto(self):
        reglas = K.Reglas(incluir_picks=True, max_por_partida=5, duracion_max_s=180, pre_s=8, post_s=3,
                          duracion_auto=False)
        clips = K.seleccionar([K.detectar_peleas(self.partida, reglas)], reglas)
        self.assertEqual(len(clips), 5)
        self.assertTrue(any(c.total == 1 for c in clips))
        self.assertLessEqual(sum(c.duracion for c in clips), 180)
        self.assertEqual(clips, sorted(clips, key=lambda c: c.clip_inicio))

    def test_primera_sangre_fuera_por_defecto(self):
        fb = self.peleas[0]
        self.assertTrue(fb.primera_sangre)
        self.assertFalse(fb.elegible)
        reglas = K.Reglas(incluir_primera_sangre=True)
        self.assertTrue(K.detectar_peleas(self.partida, reglas)[0].elegible)

    def test_el_cierre_de_la_serie_entra_siempre(self):
        reglas = K.Reglas(min_kills=5, duracion_min_s=0)   # ninguna pelea llega a 5 kills
        clips = K.seleccionar([K.detectar_peleas(self.partida, reglas)], reglas)
        self.assertEqual([c.reloj for c in clips], ["34:50"])

    def test_relleno_hasta_el_minimo(self):
        # Con min_kills 5 solo entra el cierre (35 s): se rellena por puntaje
        # con las que no pasaron el filtro hasta llegar a 90 s.
        reglas = K.Reglas(min_kills=5)
        clips = K.seleccionar([K.detectar_peleas(self.partida, reglas)], reglas)
        total = sum(c.duracion for c in clips)
        self.assertGreaterEqual(total, 90)
        self.assertLessEqual(total, 180)
        self.assertEqual(clips, sorted(clips, key=lambda c: c.clip_inicio))   # cronológico
        rellenos = [c for c in clips if c.relleno]
        self.assertEqual(len(rellenos), len(clips) - 1)
        self.assertTrue(all(not c.elegible for c in rellenos))
        # los de más puntaje entre los que no pasaron
        resto = [p for p in K.detectar_peleas(self.partida, reglas) if not p.cierra]
        mejores = sorted(resto, key=lambda p: -p.bruto)[:len(rellenos)]
        self.assertEqual(sorted(c.reloj for c in rellenos), sorted(p.reloj for p in mejores))
        # con el tope por partida
        reglas = K.Reglas(min_kills=5, max_por_partida=2)
        self.assertEqual(len(K.seleccionar([K.detectar_peleas(self.partida, reglas)], reglas)), 2)
        # sin mínimo no rellena
        reglas = K.Reglas(min_kills=5, duracion_min_s=0)
        self.assertFalse(any(c.relleno for c in K.seleccionar([K.detectar_peleas(self.partida, reglas)], reglas)))

    def test_solo_la_ultima_pelea_cierra(self):
        self.assertEqual([p.reloj for p in self.peleas if p.cierra], ["34:50"])

    def test_ganador_de_la_partida(self):
        # C9 tira las torres del nexo (18:51) justo antes del final.
        self.assertEqual(K.ganador_partida(self.partida), "C9")


# --- Partidas sintéticas -------------------------------------------------------
T0 = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
AZUL, ROJO = "KC", "C9"


def _partida(kills, oro_azul=0, minutos=30, fin=True):
    """Frames cada 1 s. `kills`: lista de (segundo, equipo, participantId).
    `oro_azul`: ventaja fija de oro del azul (cada kill suma 300 a su equipo)."""
    frames, por_jugador = [], []
    kb = kr = 0
    pk: dict[int, int] = {pid: 0 for pid in range(1, 11)}
    pendientes = sorted(kills)
    for s in range(minutos * 60 + 1):
        while pendientes and pendientes[0][0] <= s:
            _, equipo, pid = pendientes.pop(0)
            if equipo == AZUL:
                kb += 1
            else:
                kr += 1
            pk[pid] += 1
        oro = 2500 + 20 * s
        estado = "finished" if fin and s == minutos * 60 else "in_game"
        frames.append(engine.Frame(
            T0 + timedelta(seconds=s), estado,
            engine.TeamState(oro + oro_azul + 300 * kb, kb, 0, 0, 0, []),
            engine.TeamState(oro + 300 * kr, kr, 0, 0, 0, [])))
        por_jugador.append(dict(pk))
    nombres = {pid: f"{AZUL if pid <= 5 else ROJO} Jugador{pid}" for pid in range(1, 11)}
    return K.Partida("sintetica", AZUL, ROJO, frames, por_jugador, nombres)


class Duracion(unittest.TestCase):
    def test_objetivo(self):
        r = K.Reglas()
        self.assertEqual(K.objetivo_s(r, 30), 120)       # 30 min × 4 s
        self.assertEqual(K.objetivo_s(r, 20), 90)        # 80 → el mínimo
        self.assertEqual(K.objetivo_s(r, 50), 180)       # 200 → el máximo
        self.assertEqual(K.objetivo_s(K.Reglas(duracion_auto=False), 20), 180)
        self.assertEqual(K.objetivo_s(K.Reglas(duracion_min_s=60, duracion_max_s=300), 60), 240)

    def test_presupuesto_por_mapa(self):
        # Peleas de 2 kills cada 2 min: ~18 s por clip.
        def serie(minutos):
            return _partida([(s + d, ROJO, 6 + d) for s in range(120, minutos * 60 - 60, 120) for d in (0, 2)],
                            minutos=minutos)
        larga, corta = serie(40), serie(20)
        r = K.Reglas()
        clips = K.seleccionar([K.detectar_peleas(larga, r), K.detectar_peleas(corta, r)], r)
        por_mapa = {}
        for c in clips:
            por_mapa.setdefault(c.minutos_partida, []).append(c)
        self.assertEqual(sorted(por_mapa), [20, 40])
        self.assertEqual(len(por_mapa[40]), 8)                                  # tope por partida
        self.assertLessEqual(sum(c.duracion for c in por_mapa[40]), 160)        # 40 × 4
        self.assertGreater(sum(c.duracion for c in por_mapa[40]), 90)
        self.assertLessEqual(sum(c.duracion for c in por_mapa[20]), 90)         # 20 × 4 = 80 → 90
        self.assertEqual(clips, sorted(clips, key=lambda c: (c.minutos_partida != 40, c.clip_inicio)))

    def test_relleno_con_picks_y_primera_sangre(self):
        # Una sola pelea de 2 kills; el resto son picks (la primera, primera sangre).
        p = _partida([(200, ROJO, 6), (500, AZUL, 1), (800, ROJO, 7), (1100, AZUL, 2), (1400, ROJO, 8),
                      (1600, ROJO, 9), (1602, ROJO, 10)], minutos=30)
        r = K.Reglas()
        peleas = K.detectar_peleas(p, r)
        clips = K.seleccionar([peleas], r)
        self.assertEqual([c.total for c in clips if not c.relleno], [2])
        rellenos = [c for c in clips if c.relleno]
        self.assertTrue(rellenos and all(c.total == 1 for c in rellenos))
        self.assertGreaterEqual(sum(c.duracion for c in clips), 90)
        self.assertIn(True, [c.primera_sangre for c in peleas])
        self.assertEqual(clips, sorted(clips, key=lambda c: c.clip_inicio))
        for a, b in zip(clips, clips[1:]):
            self.assertLessEqual(a.clip_fin, b.clip_inicio)        # sin encimarse


def _con_torres(p, torres):
    """`torres`: lista de (segundo, equipo): ese equipo tira una torre ahí."""
    for s, equipo in torres:
        for f in p.frames[s:]:
            if equipo == AZUL:
                f.blue.towers += 1
            else:
                f.red.towers += 1
    return K.Partida(p.game_id, p.azul, p.rojo, p.frames, p.kills_jugador, p.nombres)


class PeleaDeCierre(unittest.TestCase):
    """La que entra forzada es la última pelea que GANÓ el ganador de la
    partida, no la última antes del nexo."""

    def setUp(self):
        # C9 (rojo) gana 3-0 a los 27:00 y tira las torres del nexo; KC gana
        # una escaramuza 2-0 en su base a los 29:40, 20 s antes del nexo.
        base = _partida([(1620, ROJO, 6), (1622, ROJO, 7), (1625, ROJO, 8),
                         (1780, AZUL, 1), (1782, AZUL, 2)], minutos=30)
        self.p = _con_torres(base, [(900, AZUL), (1700, ROJO), (1790, ROJO), (1795, ROJO)])

    def test_cierra_la_ultima_que_gano_el_ganador(self):
        self.assertEqual(K.ganador_partida(self.p), ROJO)
        peleas = K.detectar_peleas(self.p)
        cierre = [pe for pe in peleas if pe.cierra]
        self.assertEqual([(pe.ganador, pe.reloj) for pe in cierre], [(ROJO, "27:05")])
        self.assertEqual(cierre[0].desglose["cierre"], 30)
        self.assertIsNone(cierre[0].objetivo)              # el nexo cae 3 min después: sin "→ nexo"
        escaramuza = peleas[-1]
        self.assertEqual((escaramuza.ganador, escaramuza.cierra), (AZUL, False))
        self.assertEqual(escaramuza.desglose["cierre"], 0)
        clips = K.seleccionar([peleas])
        self.assertEqual([c.reloj for c in clips if c.forzada], ["27:05"])

    def test_entra_aunque_no_pase_el_filtro(self):
        reglas = K.Reglas(min_kills=5, duracion_min_s=0)
        clips = K.seleccionar([K.detectar_peleas(self.p, reglas)], reglas)
        self.assertEqual([(c.reloj, c.forzada) for c in clips], [("27:05", True)])

    def test_nexo_enseguida(self):
        # Si la pelea del ganador es la última y el nexo cae a los 15 s: "→ nexo".
        p = _con_torres(_partida([(1780, ROJO, 6), (1782, ROJO, 7)], minutos=30), [(1790, ROJO)])
        pe = K.detectar_peleas(p)[-1]
        self.assertTrue(pe.cierra)
        self.assertEqual(pe.objetivo, "nexo")

    def test_sin_edificios_el_oro_decide(self):
        p = _partida([(600, ROJO, 6), (602, ROJO, 7)], oro_azul=5000, minutos=20)
        self.assertEqual(K.ganador_partida(p), AZUL)        # sin torres: más oro al final
        self.assertFalse(any(pe.cierra for pe in K.detectar_peleas(p)))   # AZUL no ganó ninguna
        # dos torres en el mismo frame: también el oro
        p = _con_torres(_partida([(600, ROJO, 6)], oro_azul=5000, minutos=20), [(1100, AZUL), (1100, ROJO)])
        self.assertEqual(K.ganador_partida(p), AZUL)

    def test_partida_sin_terminar(self):
        p = _partida([(600, ROJO, 6), (602, ROJO, 7)], minutos=20, fin=False)
        self.assertIsNone(K.ganador_partida(p))
        self.assertFalse(any(pe.cierra for pe in K.detectar_peleas(p)))


class Sinteticas(unittest.TestCase):
    def test_triple_remontada_e_intercambio(self):
        # min 25: el azul va +3000; C9 (rojo) gana 3-1 con un triple de Jugador8.
        p = _partida([(1500, ROJO, 8), (1503, ROJO, 8), (1504, AZUL, 2), (1506, ROJO, 8)],
                     oro_azul=3000)
        pe = K.detectar_peleas(p)[0]
        self.assertEqual(pe.detalle, "pelea 3-1")
        self.assertEqual(pe.ganador, ROJO)
        self.assertEqual(pe.multikill, "triple de Jugador8")
        d = pe.desglose
        self.assertEqual((d["kills"], d["intercambio"], d["ganador_3"], d["multikill"], d["remontada"], d["minuto"]),
                         (80, 10, 15, 25, 15, 5))
        self.assertEqual(pe.puntaje, 100)
        self.assertIn("triple de Jugador8", pe.titulo)

    def test_sin_multikill_si_reparten_las_kills(self):
        p = _partida([(900, ROJO, 6), (903, ROJO, 7), (905, ROJO, 8)])
        pe = K.detectar_peleas(p)[0]
        self.assertIsNone(pe.multikill)
        self.assertEqual(pe.desglose["multikill"], 0)

    def test_peleas_cercanas_se_fusionan(self):
        # Kills a 16 s (> 15): dos peleas; pero sus clips quedan a < 6 s → una.
        p = _partida([(600, ROJO, 6), (602, ROJO, 7), (618, AZUL, 1), (619, AZUL, 2)])
        peleas = K.detectar_peleas(p)
        self.assertEqual(len(peleas), 1)
        self.assertEqual(peleas[0].detalle, "pelea 2-2")
        self.assertEqual(peleas[0].desglose["intercambio"], 10)

    def test_peleas_separadas_no_se_fusionan(self):
        p = _partida([(600, ROJO, 6), (602, ROJO, 7), (640, AZUL, 1), (641, AZUL, 2)])
        self.assertEqual(len(K.detectar_peleas(p)), 2)

    def test_objetivo_despues_de_la_pelea(self):
        p = _partida([(1500, ROJO, 6), (1502, ROJO, 7)])
        # Barón del rojo 20 s después de la última kill.
        for f in p.frames[1522:]:
            f.red.barons = 1
        p = K.Partida(p.game_id, p.azul, p.rojo, p.frames, p.kills_jugador, p.nombres)
        pe = K.detectar_peleas(p)[0]
        self.assertEqual(pe.objetivo, "barón")
        self.assertEqual(pe.desglose["objetivo"], 10)
        self.assertTrue(pe.titulo.startswith("C9 · pelea 2-0 → barón"))


if __name__ == "__main__":
    unittest.main()
