//! Compact RGB chart postprocessing; PDF/render/OCR engines remain Python adapters.
use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
type Error = Box<dyn std::error::Error>;
fn err(s: &str) -> Error {
    s.to_owned().into()
}
fn num(v: &Value) -> Result<f64, Error> {
    v.as_f64()
        .filter(|n| n.is_finite())
        .ok_or_else(|| err("invalid chart number"))
}
fn nums(v: &Value) -> Result<Vec<f64>, Error> {
    v.as_array()
        .ok_or("expected array")?
        .iter()
        .map(num)
        .collect()
}
fn round(v: f64) -> f64 {
    format!("{v:.1}").parse().unwrap()
}
fn iround(v: f64) -> i64 {
    v.round_ties_even() as i64
}
fn median(v: &[f64]) -> f64 {
    if v.is_empty() {
        return f64::NAN;
    }
    let mut a = v.to_vec();
    a.sort_by(f64::total_cmp);
    let n = a.len();
    if n % 2 == 0 {
        (a[n / 2 - 1] + a[n / 2]) / 2.
    } else {
        a[n / 2]
    }
}
fn clusters(indices: impl IntoIterator<Item = usize>) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    for x in indices {
        match out.last_mut() {
            Some((_, end)) if x <= *end + 1 => *end = x,
            _ => out.push((x, x)),
        }
    }
    out
}
fn fit(x: &[f64], y: &[f64]) -> (f64, f64) {
    let n = x.len() as f64;
    let mx = x.iter().sum::<f64>() / n;
    let my = y.iter().sum::<f64>() / n;
    let slope = x
        .iter()
        .zip(y)
        .map(|(a, b)| (a - mx) * (b - my))
        .sum::<f64>()
        / x.iter().map(|a| (a - mx).powi(2)).sum::<f64>();
    (slope, my - slope * mx)
}
fn intervals(n: usize) -> Vec<String> {
    (0..n)
        .map(|i| {
            if i == 0 {
                "(-∞,0]".into()
            } else {
                format!("({}, {}]", (i - 1) * 25, i * 25)
            }
        })
        .collect()
}
fn scale_values(values: &[f64], target: f64) -> Result<Vec<f64>, Error> {
    let target = round(target);
    if target <= 0. {
        return Ok(vec![0.; values.len()]);
    }
    let sum = values.iter().sum::<f64>();
    if sum <= 0. {
        return Err(err("报价 600 以上的某类电源柱形全部未识别，但表格汇总容量大于零；无法可靠分配到报价区间，请核对原图。"));
    }
    let units = iround(target * 10.);
    let quotas: Vec<f64> = values.iter().map(|v| v / sum * units as f64).collect();
    let mut allocated: Vec<i64> = quotas.iter().map(|v| *v as i64).collect();
    let remaining = units - allocated.iter().sum::<i64>();
    let mut order: Vec<usize> = (0..values.len()).collect();
    order.sort_by(|&a, &b| {
        (quotas[b] - allocated[b] as f64).total_cmp(&(quotas[a] - allocated[a] as f64))
    });
    for &i in order.iter().take(remaining.max(0) as usize) {
        allocated[i] += 1;
    }
    Ok(allocated.iter().map(|v| *v as f64 / 10.).collect())
}
fn rows(
    counts: &[[f64; 3]],
    scale: f64,
    baseline: f64,
    declaration: &Value,
) -> Result<Vec<Value>, Error> {
    if counts.is_empty() {
        return Err(err("category_count must be at least 1"));
    }
    let mut rows: Vec<[f64; 3]> = counts.iter().map(|r| r.map(|v| v * scale)).collect();
    let keys = ["coal", "gas", "renewable"];
    if declaration["zero_or_below"].is_object() {
        for c in 0..3 {
            rows[0][c] = round(num(&declaration["zero_or_below"][keys[c]])?);
        }
    } else {
        rows[0] = rows[0].map(round);
        let difference = round(round(baseline) - rows[0][0] - rows[0][1] - rows[0][2]);
        if difference.abs() >= 0.1 {
            let mut largest = 0;
            for c in 1..3 {
                if rows[0][c] > rows[0][largest] {
                    largest = c;
                }
            }
            rows[0][largest] = round(rows[0][largest] + difference);
        }
    }
    if declaration["above_600"].is_object() && rows.len() > 25 {
        for c in 0..3 {
            let values: Vec<f64> = rows[25..].iter().map(|r| r[c]).collect();
            let calibrated = scale_values(&values, num(&declaration["above_600"][keys[c]])?)?;
            for (i, v) in calibrated.into_iter().enumerate() {
                rows[25 + i][c] = v;
            }
        }
    }
    Ok(rows.iter().zip(intervals(rows.len())).map(|(r,label)|json!({"interval":label,"coal":round(r[0]+1e-9),"gas":round(r[1]+1e-9),"renewable":round(r[2]+1e-9)})).collect())
}
fn normalize(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.chars().filter(|c| !c.is_whitespace()).collect(),
        _ => v
            .to_string()
            .chars()
            .filter(|c| !c.is_whitespace())
            .collect(),
    }
}
fn first_number(v: &Value) -> Option<f64> {
    let s = match v {
        Value::Null => return None,
        Value::String(s) => s.replace('\n', ""),
        _ => v.to_string(),
    };
    let re = regex::Regex::new(r"[-+]?\d+(?:\.\d+)?").unwrap();
    re.find(&s)?.as_str().parse().ok()
}
fn declarations(p: &Value) -> Result<Value, Error> {
    let mut zero = Value::Null;
    let mut above = Value::Null;
    let keys = [
        ("申报容量", "total"),
        ("煤机容量", "coal"),
        ("气机容量", "gas"),
        ("新能源容量", "renewable"),
    ];
    for table in p["tables"].as_array().ok_or("missing tables")? {
        let mut indices: Option<HashMap<&str, usize>> = None;
        for row in table.as_array().ok_or("invalid table")? {
            let row = row.as_array().ok_or("invalid row")?;
            let cells: Vec<String> = row.iter().map(normalize).collect();
            let joined = cells.join("");
            if keys.iter().all(|(label, _)| joined.contains(label)) {
                let mut m = HashMap::new();
                for (i, cell) in cells.iter().enumerate() {
                    for (label, key) in keys {
                        if cell.contains(label) && !m.contains_key(key) {
                            m.insert(key, i);
                            break;
                        }
                    }
                }
                indices = Some(m);
                continue;
            }
            if p["require_header"].as_bool().unwrap_or(false)
                && indices.as_ref().map_or(true, |m| m.is_empty())
            {
                continue;
            }
            let slot = if joined.contains("零价") {
                &mut zero
            } else if ["报价", "600", "以上"].iter().all(|s| joined.contains(s)) {
                &mut above
            } else {
                continue;
            };
            let default = HashMap::from([("total", 1), ("coal", 4), ("gas", 9), ("renewable", 13)]);
            let map = indices
                .as_ref()
                .filter(|m| !m.is_empty())
                .unwrap_or(&default);
            let mut values = serde_json::Map::new();
            for (_, key) in keys {
                if let Some(value) = map
                    .get(key)
                    .and_then(|i| row.get(*i))
                    .and_then(first_number)
                {
                    values.insert(key.into(), json!(value));
                }
            }
            if values.len() == 4 {
                *slot = Value::Object(values);
            }
        }
    }
    Ok(json!({"zero_or_below":zero,"above_600":above}))
}
struct Image {
    w: usize,
    h: usize,
    rgb: Vec<u8>,
    masks: Vec<[bool; 5]>,
}
impl Image {
    fn load(p: &Value) -> Result<Self, Error> {
        let w = p["width"].as_u64().ok_or("missing width")? as usize;
        let h = p["height"].as_u64().ok_or("missing height")? as usize;
        let rgb = STANDARD.decode(p["pixels"].as_str().ok_or("missing compact RGB buffer")?)?;
        if w == 0 || h == 0 || w.checked_mul(h).and_then(|n| n.checked_mul(3)) != Some(rgb.len()) {
            return Err(err("invalid chart image shape"));
        }
        let masks = rgb
            .chunks_exact(3)
            .map(|v| {
                let (r, g, b) = (v[0] as i32, v[1] as i32, v[2] as i32);
                let max = r.max(g).max(b) as f64 / 255.;
                let min = r.min(g).min(b) as f64 / 255.;
                let sat = (max - min) / (max + 1e-9);
                let blue = sat > 0.1 && b > g + 8 && b > r + 18 && b > 75;
                let red = sat > 0.05 && r > b + 8 && r > g + 4 && r > 90;
                let green = sat > 0.1 && g > b + 10 && g > r + 6 && g > 85;
                let gray = (r - g).abs() < 12 && (g - b).abs() < 12 && (90..=190).contains(&r);
                [blue, red, green, gray, blue || red || green]
            })
            .collect();
        Ok(Self { w, h, rgb, masks })
    }
    fn rgb(&self, x: usize, y: usize) -> [f64; 3] {
        let i = (y * self.w + x) * 3;
        [
            self.rgb[i] as f64,
            self.rgb[i + 1] as f64,
            self.rgb[i + 2] as f64,
        ]
    }
    fn plot_rows(&self) -> Result<(usize, usize, f64), Error> {
        let candidates = clusters((21..self.h.saturating_sub(50)).filter(|&y| {
            let count = (0..self.w)
                .filter(|&x| {
                    let p = self.rgb(x, y);
                    let max = p[0].max(p[1]).max(p[2]);
                    let min = p[0].min(p[1]).min(p[2]);
                    max - min < 18. && min > 145. && max < 245.
                })
                .count();
            count as f64 > self.w as f64 * 0.45
        }));
        let rows: Vec<usize> = candidates
            .iter()
            .map(|(a, b)| (a + b) / 2)
            .filter(|&v| v > 30)
            .collect();
        if rows.len() < 5 {
            return Err(err("未能定位报价分布图的水平网格线。"));
        }
        if let Some(regular) = regular_grid(&rows) {
            let gaps: Vec<f64> = regular.windows(2).map(|a| (a[1] - a[0]) as f64).collect();
            return Ok((regular[0], *regular.last().unwrap(), median(&gaps)));
        }
        let top = rows[0];
        let gaps: Vec<f64> = rows
            .windows(2)
            .take(10)
            .map(|a| (a[1] - a[0]) as f64)
            .collect();
        let regular: Vec<f64> = gaps
            .iter()
            .copied()
            .filter(|g| *g >= 30. && *g <= 160.)
            .collect();
        let spacing = median(if regular.is_empty() {
            &gaps[..gaps.len().min(5)]
        } else {
            &regular
        });
        let base = (iround(top as f64 + 9. * spacing) as usize)
            .min(self.h - 1)
            .max(top + 10);
        Ok((top, base, spacing))
    }
    fn palette(&mut self, top: usize, base: usize) {
        let (y0, y1) = (top.saturating_sub(5), (base + 5).min(self.h));
        let green = self.masks[y0 * self.w..y1 * self.w]
            .iter()
            .filter(|m| m[2])
            .count();
        let gray = self.masks[y0 * self.w..y1 * self.w]
            .iter()
            .filter(|m| m[3])
            .count();
        if green < 300 && gray > 300 {
            for m in &mut self.masks {
                m[2] |= m[3];
                m[4] = m[0] || m[1] || m[2];
            }
        }
    }
    fn bounds(&self, top: usize) -> (usize, usize) {
        let mut best = (0, self.w - 1);
        let mut length = 0;
        for y in top.saturating_sub(3)..(top + 4).min(self.h) {
            for (a, b) in clusters((0..self.w).filter(|&x| {
                let p = self.rgb(x, y);
                (p[0] - p[1]).abs() < 8. && (p[1] - p[2]).abs() < 8. && p[0] > 120. && p[0] < 240.
            })) {
                if b - a + 1 > length {
                    length = b - a + 1;
                    best = (a, b);
                }
            }
        }
        best
    }
    fn col_counts(&self, y0: usize, y1: usize, channel: usize) -> Vec<usize> {
        (0..self.w)
            .map(|x| {
                (y0..y1)
                    .filter(|&y| self.masks[y * self.w + x][channel])
                    .count()
            })
            .collect()
    }
    fn axis(
        &self,
        top: usize,
        base: usize,
        left: usize,
        right: usize,
    ) -> Option<(f64, f64, usize)> {
        let height = base.saturating_sub(top).max(1);
        let y0 = self.h.min(base + 12.max((height as f64 * 0.02) as usize));
        let y1 = self.h.min(base + 65.max((height as f64 * 0.36) as usize));
        if y1.saturating_sub(y0) < 20 {
            return None;
        }
        let threshold = 3.max(iround((y1 - y0) as f64 * 0.02) as usize);
        let cls: Vec<(usize, usize)> = clusters((0..self.w).filter(|&x| {
            (y0..y1)
                .filter(|&y| {
                    let p = self.rgb(x, y);
                    let max = p[0].max(p[1]).max(p[2]);
                    let min = p[0].min(p[1]).min(p[2]);
                    max < 185. && max - min < 35.
                })
                .count()
                >= threshold
        }))
        .into_iter()
        .filter(|(a, b)| {
            b - a + 1 >= 2
                && (*a + *b) as f64 / 2. >= left as f64 - 8.
                && (*a + *b) as f64 / 2. <= right as f64 + 8.
        })
        .collect();
        if cls.is_empty() {
            return None;
        }
        let mut widths: Vec<f64> = cls.iter().map(|(a, b)| (b - a + 1) as f64).collect();
        widths.sort_by(f64::total_cmp);
        let index = (widths.len() - 1) as f64 * 0.75;
        let lo = index.floor() as usize;
        let hi = index.ceil() as usize;
        let min_width = 2f64.max((widths[lo] + (widths[hi] - widths[lo]) * index.fract()) * 0.3);
        let centers: Vec<f64> = cls
            .iter()
            .filter(|(a, b)| (b - a + 1) as f64 >= min_width)
            .map(|(a, b)| (a + b) as f64 / 2.)
            .collect();
        axis_fit(&centers)
    }
    fn dominant(
        &self,
        y0: usize,
        y1: usize,
        xmin: i64,
        xmax: i64,
        minheight: usize,
        channel: usize,
    ) -> Option<(f64, usize, usize, usize)> {
        let x0 = xmin.max(0) as usize;
        let x1 = (xmax.max(0) as usize).min(self.w);
        if x0 >= x1 {
            return None;
        }
        let counts = self.col_counts(y0, y1, channel);
        let mut best = None;
        let mut score = 0;
        for (a, b) in clusters((x0..self.w.min(x1 + 1)).filter(|&x| counts[x] > 2)) {
            if b - a + 1 < 2 {
                continue;
            }
            let height = *counts[a..=b].iter().max().unwrap();
            if height < minheight {
                continue;
            }
            let total = counts[a..=b].iter().sum::<usize>();
            let s = height * 1000 + total;
            if s > score {
                score = s;
                best = Some(((a + b) as f64 / 2., height, total, b - a + 1));
            }
        }
        best
    }
    fn geometry(&self, top: usize, base: usize) -> Result<Geometry, Error> {
        let (left, right) = self.bounds(top);
        let y0 = top.saturating_sub(5);
        let y1 = (base + 5).min(self.h);
        let counts = self.col_counts(y0, y1, 4);
        let cls: Vec<(usize, usize, usize)> = clusters((0..self.w).filter(|&x| counts[x] > 2))
            .into_iter()
            .filter(|(a, b)| {
                b - a + 1 >= 2
                    && (*a + *b) as f64 / 2. >= left as f64 - 5.
                    && (*a + *b) as f64 / 2. <= right as f64 + 5.
            })
            .map(|(a, b)| (a, b, *counts[a..=b].iter().max().unwrap()))
            .collect();
        if cls.is_empty() {
            return Err(err("未能定位报价分布图中的柱形。"));
        }
        let centers: Vec<f64> = cls.iter().map(|(a, b, _)| (a + b) as f64 / 2.).collect();
        let widths: Vec<f64> = cls.iter().map(|(a, b, _)| (b - a + 1) as f64).collect();
        let (first, spacing, count, source) =
            if let Some((f, s, c)) = self.axis(top, base, left, right) {
                (f, s, c, "axis-labels")
            } else {
                let (mut f, mut s) = cluster_fit(&centers);
                let width = right - left;
                let height = base.saturating_sub(top).max(1);
                let a = self.dominant(
                    y0,
                    y1,
                    left as i64 - 10,
                    (left + ((width as f64) * 0.2) as usize) as i64,
                    50.max((height as f64 * 0.12) as usize),
                    4,
                );
                let b = self.dominant(
                    y0,
                    y1,
                    right as i64 - (width as f64 * 0.25) as i64,
                    right as i64 + 10,
                    15.max((height as f64 * 0.02) as usize),
                    1,
                );
                if let (Some(a), Some(b)) = (a, b) {
                    let idx = iround((b.0 - a.0) / s);
                    if idx >= 1 {
                        let ns = (b.0 - a.0) / idx as f64;
                        if (15. ..=90.).contains(&ns) && ns >= s * 0.65 && ns <= s * 1.2 {
                            f = a.0;
                            s = ns;
                        }
                    }
                }
                (
                    f,
                    s,
                    iround((right - left) as f64 / s).max(1) as usize,
                    "plot-bounds-fallback",
                )
            };
        let half = iround(median(&widths) / 2. + 5.).clamp(7, 15) as usize;
        Ok(Geometry {
            first,
            spacing,
            count,
            half,
            clusters: cls.len(),
            source,
        })
    }
}
struct Geometry {
    first: f64,
    spacing: f64,
    count: usize,
    half: usize,
    clusters: usize,
    source: &'static str,
}
fn regular_grid(rows: &[usize]) -> Option<Vec<usize>> {
    let mut bests = HashMap::new();
    for size in [11, 10] {
        if rows.len() < size {
            continue;
        }
        let mut best: Option<(Vec<usize>, f64, f64)> = None;
        for window in rows.windows(size) {
            let gaps: Vec<f64> = window.windows(2).map(|a| (a[1] - a[0]) as f64).collect();
            if gaps.iter().any(|g| *g < 40. || *g > 160.) {
                continue;
            }
            let med = median(&gaps);
            let dev = gaps.iter().map(|g| (g - med).abs()).fold(0., f64::max);
            if dev > 4f64.max(med * 0.08) {
                continue;
            }
            let mean = gaps.iter().sum::<f64>() / gaps.len() as f64;
            let score = (gaps.iter().map(|g| (g - mean).powi(2)).sum::<f64>() / gaps.len() as f64)
                .sqrt()
                + dev * 0.25;
            if best.as_ref().map_or(true, |b| score < b.1) {
                best = Some((window.to_vec(), score, dev));
            }
        }
        if let Some(b) = best {
            bests.insert(size, b);
        }
    }
    if let Some(b) = bests.get(&11) {
        if b.2 <= 4. {
            return Some(b.0.clone());
        }
    }
    bests.get(&10).map(|b| b.0.clone())
}
fn axis_fit(centers: &[f64]) -> Option<(f64, f64, usize)> {
    if centers.len() < 5 {
        return None;
    }
    let mut values = centers.to_vec();
    values.sort_by(f64::total_cmp);
    let gaps: Vec<f64> = values
        .windows(2)
        .map(|a| a[1] - a[0])
        .filter(|g| *g > 3.)
        .collect();
    if gaps.len() < 4 {
        return None;
    }
    let mut spacing = median(&gaps);
    if !(12. ..=90.).contains(&spacing) {
        return None;
    }
    let mut first = values[0];
    let mut selected = BTreeMap::new();
    for _ in 0..4 {
        selected.clear();
        for &center in &values {
            let index = iround((center - first) / spacing);
            if index < 0 {
                continue;
            }
            let residual = (center - (first + index as f64 * spacing)).abs();
            if residual > spacing * 0.2 {
                continue;
            }
            if selected.get(&index).map_or(true, |&(r, _)| residual < r) {
                selected.insert(index, (residual, center));
            }
        }
        if selected.len() < 5 {
            return None;
        }
        let x: Vec<f64> = selected.keys().map(|v| *v as f64).collect();
        let y: Vec<f64> = selected.values().map(|v| v.1).collect();
        let (s, f) = fit(&x, &y);
        if !(12. ..=90.).contains(&s) {
            return None;
        }
        first = f;
        spacing = s;
    }
    let count = *selected.keys().last()? as usize + 1;
    if count < 5 || (selected.len() as f64) < count as f64 * 0.8 {
        return None;
    }
    Some((first, spacing, count))
}
fn cluster_fit(centers: &[f64]) -> (f64, f64) {
    let gaps: Vec<f64> = centers
        .windows(2)
        .map(|v| v[1] - v[0])
        .filter(|g| *g >= 15. && *g <= 90.)
        .collect();
    let mut spacing = if gaps.is_empty() { 37. } else { median(&gaps) };
    let mut first = centers[0];
    let mut grouped: BTreeMap<i64, Vec<f64>> = BTreeMap::new();
    for &c in centers {
        let index = iround((c - first) / spacing);
        if (0..=36).contains(&index) {
            grouped.entry(index).or_default().push(c);
        }
    }
    if grouped.len() >= 4 {
        let x: Vec<f64> = grouped.keys().map(|v| *v as f64).collect();
        let y: Vec<f64> = grouped.values().map(|v| median(v)).collect();
        let (s, f) = fit(&x, &y);
        if (15. ..=60.).contains(&s) && (f - first).abs() <= spacing {
            spacing = s;
            first = f;
        }
    }
    (first, spacing)
}

pub fn execute(p: &Value) -> Result<Value, Error> {
    match p["action"].as_str().ok_or("missing pdf_chart action")? {
        "scale_values" => Ok(json!(scale_values(
            &nums(&p["values"])?,
            num(&p["target"])?
        )?)),
        "declarations" => declarations(p),
        "rows" => {
            let counts = parse_counts(&p["counts"])?;
            Ok(json!(rows(
                &counts,
                num(&p["scale"])?,
                num(&p["baseline"])?,
                &p["declaration"]
            )?))
        }
        "analyze_chart" => analyze(p),
        "score_chart" => score_chart(p),
        "baseline_text" => baseline_text(p),
        _ => Err(err("unknown pdf_chart action")),
    }
}
fn parse_counts(v: &Value) -> Result<Vec<[f64; 3]>, Error> {
    v.as_array()
        .ok_or("missing counts")?
        .iter()
        .map(|r| {
            let a = nums(r)?;
            if a.len() != 3 {
                return Err(err("invalid counts"));
            }
            Ok([a[0], a[1], a[2]])
        })
        .collect()
}
fn analyze(p: &Value) -> Result<Value, Error> {
    analyze_chart(p)
}
fn f64_buffer(v: impl IntoIterator<Item = f64>) -> String {
    let bytes: Vec<u8> = v.into_iter().flat_map(f64::to_le_bytes).collect();
    STANDARD.encode(bytes)
}
fn profile(
    pixels: &[u8],
    width: usize,
    bg: &[[f64; 3]],
    palette: &[Option<[f64; 3]>; 3],
    filter: bool,
) -> Result<Vec<[f64; 3]>, Error> {
    let colors: Vec<Value> = palette
        .iter()
        .map(|c| c.map_or(Value::Null, |v| json!(f64_buffer(v))))
        .collect();
    let result = crate::compute::unmix(
        &json!({"height":bg.len(),"width":width,"uint8":true,"profile":true,"pixels":STANDARD.encode(pixels),"background":f64_buffer(bg.iter().flatten().copied()),"palette":colors}),
    )?;
    let bytes = STANDARD.decode(result["coverage"].as_str().ok_or("invalid unmix profile")?)?;
    let f: Vec<f64> = bytes
        .chunks_exact(8)
        .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
        .collect();
    let out: Vec<[f64; 3]> = f.chunks_exact(3).map(|v| [v[0], v[1], v[2]]).collect();
    if out.len() != bg.len() {
        return Err(err("invalid profile height"));
    }
    if filter {
        let supported = supported_sources(pixels, width, bg, palette, &out);
        if (0..3).any(|c| palette[c].is_some() && !supported[c]) {
            let restricted = std::array::from_fn(|c| if supported[c] { palette[c] } else { None });
            return profile(pixels, width, bg, &restricted, false);
        }
    }
    Ok(out)
}
fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}
fn mean(a: [f64; 3]) -> f64 {
    a.iter().sum::<f64>() / 3.
}
fn chroma(a: [f64; 3]) -> [f64; 3] {
    let m = mean(a);
    a.map(|v| v - m)
}
fn supported_sources(
    pixels: &[u8],
    w: usize,
    bg: &[[f64; 3]],
    palette: &[Option<[f64; 3]>; 3],
    profile: &[[f64; 3]],
) -> [bool; 3] {
    let mut supported = [false; 3];
    for y in 0..bg.len() {
        let rgb: [f64; 3] = std::array::from_fn(|c| {
            median(
                &(0..w)
                    .map(|x| pixels[(y * w + x) * 3 + c] as f64)
                    .collect::<Vec<_>>(),
            )
        });
        let delta = std::array::from_fn(|c| rgb[c] - bg[y][c]);
        let chroma = chroma(delta);
        let cn = dot(chroma, chroma).sqrt();
        let total = profile[y].iter().sum::<f64>();
        let visible = total > 0.08 && mean(bg[y]) >= 245.;
        let mut cosines = [-1.; 3];
        let mut predicted = bg[y];
        for c in 0..3 {
            if let Some(color) = palette[c] {
                let direction = std::array::from_fn(|k| color[k] - bg[y][k]);
                let cc = self::chroma(direction);
                let norm = dot(cc, cc).sqrt();
                let cosine = dot(chroma, cc) / (cn * norm).max(1.);
                cosines[c] = cosine;
                let hue = cosine > 0.94 && cn > 1.5 && norm > 20.;
                let gray = norm < 20. && cn < 12. && profile[y][c] > 0.08;
                supported[c] |= visible && (hue || gray);
                for k in 0..3 {
                    predicted[k] += profile[y][c] * direction[k];
                }
            }
        }
        let exact = total < 0.95
            && (0..3)
                .map(|k| (rgb[k] - predicted[k]).abs())
                .fold(0., f64::max)
                < 2.;
        let mixed = visible && (cosines.iter().copied().fold(-1., f64::max) < 0.9 || exact);
        for c in 0..3 {
            supported[c] |= mixed && profile[y][c] / total.max(1e-9) >= 0.2;
        }
    }
    supported
}
fn measurement(
    image: &Image,
    top: usize,
    base: usize,
    g: &Geometry,
) -> Result<Vec<[f64; 3]>, Error> {
    let y0 = top.saturating_sub(5);
    let y1 = (base + 1).min(image.h);
    let left = (g.first - g.spacing / 2.).max(0.) as usize;
    let right = ((g.first + (g.count as f64 - 0.5) * g.spacing) as usize).min(image.w);
    if left >= right {
        return Err(err("empty chart background range"));
    }
    let bg: Vec<[f64; 3]> = (y0..y1)
        .map(|y| {
            std::array::from_fn(|c| {
                median(
                    &(left..right)
                        .map(|x| image.rgb(x, y)[c])
                        .collect::<Vec<_>>(),
                )
            })
        })
        .collect();
    let contrast = |x: usize, y: usize| {
        let rgb = image.rgb(x, y + y0);
        (0..3).map(|c| (rgb[c] - bg[y][c]).abs()).fold(0., f64::max)
    };
    let mut interiors = Vec::new();
    for idx in 0..g.count {
        let center = g.first + idx as f64 * g.spacing;
        let radius = g.half.min((g.spacing * 0.4) as usize).max(1);
        let x0 = (iround(center - radius as f64).max(0) as usize).min(image.w);
        let x1 = (iround(center + radius as f64 + 1.).max(0) as usize).min(image.w);
        if x0 >= x1 {
            interiors.push((x0, x0));
            continue;
        }
        let strength: Vec<usize> = (x0..x1)
            .map(|x| {
                (0..bg.len())
                    .filter(|&y| image.masks[(y + y0) * image.w + x][4] && contrast(x, y) > 25.)
                    .count()
            })
            .collect();
        let threshold = 1f64.max(*strength.iter().max().unwrap() as f64 * 0.5);
        let runs: Vec<(usize, usize)> =
            clusters((0..strength.len()).filter(|&x| strength[x] as f64 >= threshold))
                .into_iter()
                .filter(|(a, b)| b - a + 1 >= 3)
                .collect();
        if let Some((a, b)) = runs.iter().min_by(|a, b| {
            ((x0 as f64 + (a.0 + a.1) as f64 / 2.) - center)
                .abs()
                .total_cmp(&((x0 as f64 + (b.0 + b.1) as f64 / 2.) - center).abs())
        }) {
            let trim = ((b - a + 1) as f64 * 0.2) as usize;
            interiors.push((x0 + a + trim, x0 + b + 1 - trim));
        } else {
            let middle = iround(center);
            interiors.push((
                x0.max((middle - 2).max(0) as usize),
                x1.min((middle + 3).max(0) as usize),
            ));
        }
    }
    let mut palette = [None; 3];
    for c in 0..3 {
        let mut pixels = Vec::new();
        for &(x0, x1) in &interiors {
            for y in y0..y1 {
                for x in x0..x1 {
                    if image.masks[y * image.w + x][c] && contrast(x, y - y0) > 35. {
                        let rgb = image.rgb(x, y);
                        pixels.push(rgb);
                    }
                }
            }
        }
        if pixels.is_empty() {
            continue;
        }
        let mut bins: BTreeMap<[u8; 3], usize> = BTreeMap::new();
        for rgb in &pixels {
            *bins.entry(rgb.map(|v| v as u8 / 16)).or_default() += 1;
        }
        let mut dominant = [0; 3];
        let mut count = 0;
        for (bin, n) in bins {
            if n > count {
                count = n;
                dominant = bin;
            }
        }
        let near: Vec<[f64; 3]> = pixels
            .into_iter()
            .filter(|rgb| {
                (0..3)
                    .map(|k| (rgb[k] - (dominant[k] as f64 * 16. + 7.5)).abs())
                    .fold(0., f64::max)
                    < 24.
            })
            .collect();
        palette[c] = Some(std::array::from_fn(|k| {
            median(&near.iter().map(|v| v[k]).collect::<Vec<_>>())
        }));
    }
    let mut rows = Vec::new();
    for (x0, x1) in interiors {
        if x0 >= x1 {
            rows.push([0.; 3]);
            continue;
        }
        let mut pixels = Vec::with_capacity(bg.len() * (x1 - x0) * 3);
        for y in y0..y1 {
            pixels.extend_from_slice(&image.rgb[(y * image.w + x0) * 3..(y * image.w + x1) * 3]);
        }
        let mut profile = profile(&pixels, x1 - x0, &bg, &palette, true)?;
        let active: Vec<usize> = (0..profile.len())
            .filter(|&y| profile[y].iter().sum::<f64>() > 0.08)
            .collect();
        let max_gap = 3.max((base.saturating_sub(top) as f64 * 0.008) as usize);
        for pair in active.windows(2) {
            let (before, after) = (pair[0], pair[1]);
            if after - before > 1
                && after - before <= max_gap + 1
                && bg[before + 1..after].iter().any(|&v| mean(v) < 245.)
            {
                for y in before + 1..after {
                    for c in 0..3 {
                        profile[y][c] = profile[before][c]
                            + (profile[after][c] - profile[before][c]) * (y - before) as f64
                                / (after - before) as f64;
                    }
                }
            }
        }
        let active = (0..profile.len()).filter(|&y| profile[y].iter().sum::<f64>() > 0.08);
        let mut runs: Vec<(usize, usize)> = Vec::new();
        for (a, b) in clusters(active) {
            if let Some(last) = runs.last_mut() {
                if a - last.1 <= max_gap + 1 {
                    last.1 = b;
                    continue;
                }
            }
            runs.push((a, b));
        }
        let grounded: Vec<_> = runs
            .into_iter()
            .filter(|(_, b)| profile.len() - 1 - b <= max_gap)
            .collect();
        let score = |(a, b): (usize, usize)| profile[a..=b].iter().flatten().sum::<f64>();
        if let Some(&(a, b)) = grounded
            .iter()
            .max_by(|&&a, &&b| score(a).total_cmp(&score(b)))
        {
            let start = a.saturating_sub(1);
            let end = (b + 2).min(profile.len());
            rows.push(std::array::from_fn(|c| {
                profile[start..end].iter().map(|r| r[c]).sum()
            }));
        } else {
            rows.push([0.; 3]);
        }
    }
    Ok(rows)
}
fn analyze_chart(p: &Value) -> Result<Value, Error> {
    let mut image = Image::load(p)?;
    let (top, base, ys) = image.plot_rows()?;
    image.palette(top, base);
    let g = image.geometry(top, base)?;
    let counts = measurement(&image, top, base, &g)?;
    let first_total = counts[0].iter().sum::<f64>();
    if first_total <= 0. {
        return Err(err("未能识别报价分布图中的零价及以下柱形。"));
    }
    let baseline = num(&p["baseline"])?;
    let scale = baseline / first_total;
    let rows = rows(&counts, scale, baseline, &p["declaration"])?;
    let mut low = Vec::new();
    for (label, row) in intervals(counts.len()).iter().zip(&counts) {
        for (source, value) in ["燃煤", "燃气", "新能源"].iter().zip(row) {
            if *value > 0. && *value < 3. {
                low.push(json!({"interval":label,"source":source,"height_pixels":(*value*1000.).round_ties_even()/1000.}));
            }
        }
    }
    let mut diagnostics = json!({"height_measurement":"subpixel-color-coverage","color_validation":"bar-hue-evidence","plot_top_y":top,"plot_baseline_y":base,"y_grid_spacing":ys,"first_category_center_x":g.first,"category_spacing_x":g.spacing,"category_half_width":g.half,"category_count":g.count,"category_geometry_source":g.source,"first_total_pixels":first_total,"detected_x_cluster_count":g.clusters,"scale_mw_per_pixel":scale,"low_height_segments":low});
    if p["declaration"].is_object() {
        diagnostics["declaration_zero_or_below"] = p["declaration"]["zero_or_below"].clone();
        diagnostics["declaration_above_600"] = p["declaration"]["above_600"].clone();
    }
    Ok(
        json!({"rows":rows,"pixel_counts":counts,"scale_mw_per_pixel":scale,"diagnostics":diagnostics}),
    )
}
fn score_chart(p: &Value) -> Result<Value, Error> {
    let mut image = Image::load(p)?;
    if image.w < 400 || image.h < 180 {
        return Ok(json!(0.));
    }
    let (top, base, _) = match image.plot_rows() {
        Ok(v) => v,
        Err(_) => return Ok(json!(0.)),
    };
    image.palette(top, base);
    let y0 = top.saturating_sub(5);
    let y1 = (base + 5).min(image.h);
    let counts = image.col_counts(y0, y1, 4);
    let cls: Vec<_> = clusters((0..image.w).filter(|&x| counts[x] > 2))
        .into_iter()
        .filter(|(a, b)| b - a >= 2)
        .collect();
    if cls.is_empty() {
        return Ok(json!(0.));
    }
    let centers: Vec<f64> = cls.iter().map(|(a, b)| (a + b) as f64 / 2.).collect();
    let regular = centers
        .windows(2)
        .filter(|v| v[1] - v[0] >= 15. && v[1] - v[0] <= 100.)
        .count();
    let x0 = cls[0].0.saturating_sub(3);
    let x1 = (cls[0].1 + 4).min(image.w);
    let first_height = (y0..y1)
        .filter(|&y| {
            (x0..x1)
                .filter(|&x| image.masks[y * image.w + x][4])
                .count()
                > 2
        })
        .count();
    let mut score = regular as f64 * 2. + (first_height as f64 / 30.).min(20.);
    if image.w as f64 / (image.h.max(1) as f64) < 1.6 {
        score -= 8.
    }
    Ok(json!(score))
}
fn baseline_text(p: &Value) -> Result<Value, Error> {
    let text = p["text"].as_str().ok_or("missing text")?;
    let re = regex::Regex::new(r"[-+]?\d+(?:\.\d+)?")?;
    for marker in ["零价及以", "零价及以下"] {
        if let Some(start) = text.find(marker) {
            let tail = &text[start..];
            let next = tail[marker.len()..].find("报价").map(|i| marker.len() + i);
            let snippet = if let Some(end) = next {
                tail[..end].to_string()
            } else {
                tail.chars().take(400).collect()
            };
            for m in re.find_iter(&snippet) {
                if let Ok(v) = m.as_str().parse::<f64>() {
                    if v > 1000. {
                        return Ok(json!({"value":v}));
                    }
                }
            }
        }
    }
    Ok(json!({"value":null}))
}
