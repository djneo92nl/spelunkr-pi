# Fonts

`star4000.ttf` ("STAR 4"), `star4000-large.ttf` ("Star4 Large") and `star4000-extended.ttf` ("STAR 4 Extended") are the WeatherStar 4000
style pixel fonts that spelunkr.tv itself uses for its look. They were fetched from the site's own font files
(`https://spelunkr.tv/fonts/star4000/*.woff2`) and converted to TrueType with `tools/woff2_to_ttf.py`, because libass (mpv's overlay renderer)
cannot read WOFF2:

    curl -O https://spelunkr.tv/fonts/star4000/star4000.woff2     # and -large, -extended
    python3 tools/woff2_to_ttf.py star4000*.woff2 --out fonts/

I did not find a licence file for them. Check the font's licence before you share this folder outside your own devices.

mpv loads them with `--osd-fonts-dir` (added automatically when your mpv supports it); otherwise install them system-wide
(`pi/setup.sh` does that on the Pi). Without them the overlays fall back to the old Arial-style look. `"pixel_fonts": false` in config.json forces that.
