from flask import Flask, render_template_string

app = Flask("PolyEarn")

MONETAG_DIRECT_LINK = "https://monetag.com" 

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Poly Earn</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-950 text-white font-sans min-h-screen p-4 flex flex-col items-center">
    <div class="w-full max-w-sm bg-gray-900 border border-green-500/30 rounded-3xl p-6 shadow-2xl mt-6">
        <div class="text-center mb-6">
            <span class="text-xs uppercase tracking-widest text-gray-400 font-semibold">Poly Earn Balance</span>
            <h1 class="text-4xl font-extrabold text-green-400 mt-1">250 Points</h1>
            <p class="text-xs text-gray-400 font-medium mt-1">≈ <span class="text-white font-bold">$2.50 USDT</span></p>
        </div>
        <button onclick="watchAd()" class="w-full bg-green-500 hover:bg-green-400 text-gray-950 font-black py-3.5 rounded-2xl transition duration-200 cursor-pointer shadow-lg shadow-green-500/20 active:scale-95 mb-6">
            Watch Ad (+2 Points)
        </button>
    </div>
    <script>
        function watchAd() {
            window.open("{{ monetag_link }}", "_blank");
            alert("Ad opened! Points credited.");
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML, monetag_link=MONETAG_DIRECT_LINK)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
