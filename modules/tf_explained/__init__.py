"""«La TF explicada»: the engine of the video type that stops a teamfight of
the LoL competitive scene to explain it (a narrated voice, the fight slowed
down or frozen where the play happens, annotations and data cards).

The recipe (how a video of this type is made, what was learned) is in the
PKB: redes-sociales/creador-de-contenido-axelsine/worlds-2026/receta-la-tf-explicada.md.

  dossier   the official feed around a fight: per player HP, gold, K/D/A,
            items and wards, in the scoreboard clock (pauses included), and
            the fight's timeline (deaths with killer and assists, HP per second)
  vod       the spans of the VOD by scoreboard clock
  sheets    contact sheets to read the fight frame by frame (a crop of the
            broadcast, or the frame + its minimap + the HP of the 10)
  layout    the analysis layout (scoreboard, big gameplay, band with the
            minimap) and the broadcast → output mapping for annotations
  plan      clock-based pieces for the EDL (run, slow, freeze)
  montage   the narrated base through the Studio: screen words in the
            script's spelling, slides placed by the marked script, montage
            with subtitles

No routes and no jobs: Claude operates it from a script, like the editing
engine. FFmpeg only through core.ffmpeg_runner.
"""
