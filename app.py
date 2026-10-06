import os, re, glob, json, time, shutil, subprocess, requests
from flask import Flask, request, jsonify, send_file

H = {"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"}
app = Flask(__name__)
os.makedirs("cache", exist_ok=True)
CHUNK = 600

def post(url, **kw):
    for _ in range(5):
        r = requests.post(url, headers=H, **kw)
        if r.status_code == 429:
            time.sleep(20); continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Bị giới hạn tốc độ")

def translate(batch, lang):
    text = "\n".join(f"{n}|{t}" for n, (_, _, t) in enumerate(batch, 1))
    d = post("https://api.groq.com/openai/v1/chat/completions", json={
        "model": "llama-3.3-70b-versatile", "temperature": 0.2,
        "messages": [
            {"role": "system", "content":
             f"Bạn là dịch giả phụ đề chuyên nghiệp. Dịch sang {lang}, tự nhiên, ngắn gọn. "
             "Giữ định dạng 'số|câu dịch', mỗi dòng một câu, không giải thích."},
            {"role": "user", "content": text}]})
    out = {}
    for line in d["choices"][0]["message"]["content"].splitlines():
        m = re.match(r"\s*(\d+)\|(.*)", line)
        if m: out[int(m[1])] = m[2].strip()
    return [out.get(n, batch[n-1][2]) for n in range(1, len(batch)+1)]

@app.route("/")
def index():
    return send_file("index.html")

@app.route("/api/process")
def process():
    url = request.args["url"]
    lang = request.args.get("lang", "Vietnamese")
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url)
    if not m:
        return jsonify(error="Link YouTube không hợp lệ"), 400
    vid = m[1]
    cf = f"cache/{vid}_{lang}.json"
    if os.path.exists(cf):
        return jsonify(vid=vid, segs=json.load(open(cf, encoding="utf-8")))
    tmp = f"tmp_{vid}"
    shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    try:
        subprocess.run(["yt-dlp", "-x", "-f", "bestaudio", "-o", f"{tmp}/raw.%(ext)s", url], check=True)
        raw = glob.glob(f"{tmp}/raw.*")[0]
        subprocess.run(["ffmpeg", "-y", "-i", raw, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
                        "-f", "segment", "-segment_time", str(CHUNK), f"{tmp}/part_%03d.mp3"], check=True)
        segs = []
        for i, p in enumerate(sorted(glob.glob(f"{tmp}/part_*.mp3"))):
            with open(p, "rb") as f:
                d = post("https://api.groq.com/openai/v1/audio/transcriptions", files={"file": f},
                         data={"model": "whisper-large-v3", "response_format": "verbose_json"})
            for s in d["segments"]:
                segs.append((s["start"] + i*CHUNK, s["end"] + i*CHUNK, s["text"].strip()))
        vi = []
        for k in range(0, len(segs), 30):
            vi += translate(segs[k:k+30], lang)
        res = [{"s": a, "e": b, "t": t, "v": v} for (a, b, t), v in zip(segs, vi)]
        json.dump(res, open(cf, "w", encoding="utf-8"), ensure_ascii=False)
        return jsonify(vid=vid, segs=res)
    except Exception as ex:
        return jsonify(error=str(ex)), 500
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.route("/manifest.json")
@app.route("/sw.js")
@app.route("/icon192.png")
@app.route("/icon512.png")
def files():
    return send_file(request.path[1:])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
