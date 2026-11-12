#pragma once
#include "resample.hpp"
#include <deque>

// A native 1X frame is a slot, not a picture: the artwork it paints usually keeps real margins
// inside it. Filling the whole slot with a master therefore draws the artwork larger than the
// game ever drew it - on the adventure map that spills onto neighbouring tiles (a mountain
// standing on the road), in the main menu it makes buttons touch. The box computed here is the
// extent the official frame actually paints, so it is the box a master artwork has to be fitted
// into. Only the frame's own alpha is trusted, plus one narrow exception: when the frame corner
// is opaque the extraction carried a flat matte, and matte pixels that are connected to the
// border and exactly equal to the corner colour are background, not art.
namespace h3tier {
inline constexpr float SILHOUETTE_SOLID = 1.f / 32.f;
struct ArtworkBox { int x0 = 0, y0 = 0, x1 = 0, y1 = 0; bool empty = true; bool matte = false; };
inline ArtworkBox artwork_box(const Image& im, bool stripMatte = false) {
	ArtworkBox b; auto solid = [&](int x, int y) { return im.p[std::size_t(y) * im.w + x].a > SILHOUETTE_SOLID; };
	std::vector<std::uint8_t> keep(std::size_t(im.w) * im.h);
	auto corner = im.p[0];
	b.matte = stripMatte && corner.a > 1.f - SILHOUETTE_SOLID;
	for (int y = 0; y < im.h; y++) for (int x = 0; x < im.w; x++)
		keep[std::size_t(y) * im.w + x] = solid(x, y) ? 1 : 0;
	if (b.matte) { // border-connected pixels that equal the opaque corner colour are the matte
		std::vector<std::uint8_t> keyed(std::size_t(im.w) * im.h);
		std::deque<int> q;
		auto matte_at = [&](int x, int y) {
			auto& px = im.p[std::size_t(y) * im.w + x];
			if (!keyed[std::size_t(y) * im.w + x] && (!keep[std::size_t(y) * im.w + x]
				|| (px.r == corner.r && px.g == corner.g && px.b == corner.b))) {
				keyed[std::size_t(y) * im.w + x] = 1; q.push_back(y * im.w + x);
			}
		};
		for (int x = 0; x < im.w; x++) { matte_at(x, 0); matte_at(x, im.h - 1); }
		for (int y = 0; y < im.h; y++) { matte_at(0, y); matte_at(im.w - 1, y); }
		while (!q.empty()) {
			int i = q.front(); q.pop_front(); int x = i % im.w, y = i / im.w;
			if (x > 0) matte_at(x - 1, y);
			if (x + 1 < im.w) matte_at(x + 1, y);
			if (y > 0) matte_at(x, y - 1);
			if (y + 1 < im.h) matte_at(x, y + 1);
		}
		for (std::size_t i = 0; i < keep.size(); i++) if (keyed[i]) keep[i] = 0;
		if (!std::any_of(keep.begin(), keep.end(), [](std::uint8_t v) { return v; }))
			return artwork_box(im, false); // a fully matte frame carries no usable silhouette
	}
	int fx = im.w, fy = im.h, lx = -1, ly = -1;
	for (int y = 0; y < im.h; y++) for (int x = 0; x < im.w; x++) if (keep[std::size_t(y) * im.w + x]) {
		fx = std::min(fx, x); fy = std::min(fy, y); lx = std::max(lx, x); ly = std::max(ly, y);
	}
	if (lx < 0) return b;
	b = { fx, fy, lx + 1, ly + 1, false, b.matte };
	return b;
}
// Where the fitted artwork lands inside the official slot. Map objects hang off their bottom
// edge, so the artwork keeps the slot's bottom-right corner; interface art is centred.
inline const char* silhouette_place(int canvasW, int canvasH, int boxX0, int boxY0, int boxX1, int boxY1,
	int artW, int artH, const std::string& anchor, int& outX, int& outY) {
	if (anchor == "bottom_right") { outX = boxX1 - artW; outY = boxY1 - artH; }
	else if (anchor == "center") { outX = boxX0 + ((boxX1 - boxX0) - artW) / 2; outY = boxY0 + ((boxY1 - boxY0) - artH) / 2; }
	else return "fit_anchor must be bottom_right or center";
	if (outX < boxX0 || outY < boxY0 || outX + artW > boxX1 || outY + artH > boxY1)
		return "fitted artwork escapes the official silhouette box";
	if (outX < 0 || outY < 0 || outX + artW > canvasW || outY + artH > canvasH)
		return "fitted artwork escapes the official slot";
	return nullptr;
}
// In silhouette mode the master's canvas ratio no longer has to match the slot: the artwork is
// measured and fitted, so a square master in a wide slot is normal. What must not happen silently
// is a master whose shape is nothing like the official artwork - it would shrink into a strip.
// The floor below is set from the accepted batches: 153 map objects fit at 0.627..1.000.
inline constexpr double SILHOUETTE_COVERAGE_FLOOR=0.50;
// Master artwork, cropped to its own visible extent, uniformly resampled into the official
// silhouette box and pasted onto a transparent slot-sized canvas. Deterministic: the same call
// is used to write the PNG and to replay it during verification.
inline Image silhouette_expand_cpu(const Image& master, int w, int h, const ArtworkBox& slotBox,
	const ArtworkBox& art, const std::string& anchor) {
	need(!art.empty && !slotBox.empty, "silhouette expansion needs artwork in both master and official frame");
	int aw = art.x1 - art.x0, ah = art.y1 - art.y0, bw = slotBox.x1 - slotBox.x0, bh = slotBox.y1 - slotBox.y0;
	need(aw > 0 && ah > 0 && bw > 0 && bh > 0, "empty silhouette box");
	double scale = std::min(double(bw) / aw, double(bh) / ah);
	int nw = std::max(1, int(std::lround(aw * scale))), nh = std::max(1, int(std::lround(ah * scale)));
	if (nw > bw) nw = bw;
	if (nh > bh) nh = bh;
	double coverage = std::min(double(nw) / bw, double(nh) / bh);
	need(coverage >= SILHOUETTE_COVERAGE_FLOOR,
		"master artwork covers only " + std::to_string(int(coverage * 100)) + "% of the official silhouette on its shorter axis (floor "
		+ std::to_string(int(SILHOUETTE_COVERAGE_FLOOR * 100)) + "%); the master shape does not belong to this slot - re-derive it or register it through scale-layout");
	Image crop(aw, ah);
	for (int y = 0; y < ah; y++) for (int x = 0; x < aw; x++) crop.p[std::size_t(y) * aw + x] = master.p[std::size_t(y + art.y0) * master.w + x + art.x0];
	auto fitted = quality_resize_cpu(crop, nw, nh);
	int px = 0, py = 0; auto why = silhouette_place(w, h, slotBox.x0, slotBox.y0, slotBox.x1, slotBox.y1, nw, nh, anchor, px, py);
	if (why) need(false, why); // need() evaluates its message eagerly: never build a std::string from a null reason
	Image out(w, h);
	for (int y = 0; y < nh; y++) for (int x = 0; x < nw; x++) out.p[std::size_t(y + py) * w + x + px] = fitted.p[std::size_t(y) * nw + x];
	return out;
}
}
