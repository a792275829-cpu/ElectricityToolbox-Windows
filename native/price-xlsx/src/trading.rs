//! Bulk trading parsing and analysis. Python retains the document rendering layer.
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
type Result<T> = std::result::Result<T, Box<dyn Error>>;
const THERMAL: [&str; 3] = ["汕头", "海门", "东莞"];
const RENEWABLE: [&str; 3] = ["海风", "鮀莲", "归湖"];
const TYPES: [&str; 4] = [
    "高价欠发亏损",
    "高价超发收益",
    "低价欠发收益",
    "低价超发亏损",
];
thread_local! { static COMPENSATED_SUM: std::cell::Cell<bool> = const { std::cell::Cell::new(true) }; }
fn py_sum(values: impl Iterator<Item = f64>) -> f64 {
    if !COMPENSATED_SUM.with(|mode| mode.get()) {
        return values.sum();
    }
    let (mut hi, mut lo) = (0.0_f64, 0.0_f64);
    for value in values {
        let t = hi + value;
        lo += if hi.abs() >= value.abs() {
            (hi - t) + value
        } else {
            (value - t) + hi
        };
        hi = t;
    }
    if lo != 0.0 && lo.is_finite() {
        hi + lo
    } else {
        hi
    }
}
fn n(v: &Value) -> f64 {
    v.as_f64().filter(|x| x.is_finite()).unwrap_or(0.)
}
fn nv(v: &Value) -> Value {
    v.as_f64()
        .filter(|x| x.is_finite())
        .map_or(Value::Null, Value::from)
}
fn text(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::String(s) => s.clone(),
        _ => v.to_string(),
    }
}
fn label(v: &Value) -> String {
    if v.is_null() || v == false || v == 0 || v == "" {
        String::new()
    } else {
        text(v)
    }
}
fn cell(rows: &Value, r: usize, c: usize) -> &Value {
    rows.get(r - 1)
        .and_then(|x| x.get(c - 1))
        .unwrap_or(&Value::Null)
}
fn field(v: &Value, k: &str) -> f64 {
    n(&v[k])
}
fn list(v: &Value) -> &[Value] {
    v.as_array().map(Vec::as_slice).unwrap_or(&[])
}
fn merge(segments: &Value) -> Value {
    let mut out: Vec<Value> = Vec::new();
    for s in list(segments) {
        if let Some(last) = out.last_mut() {
            if (field(last, "price") - field(s, "price")).abs() < 1e-9
                && (field(last, "end") - field(s, "start")).abs() < 1e-9
            {
                last["end"] = s["end"].clone();
                last["source_count"] = json!(last["source_count"].as_u64().unwrap() + 1);
                continue;
            }
        }
        let mut copy = s.clone();
        copy["source_count"] = json!(1);
        out.push(copy);
    }
    json!(out)
}
fn classify(item: &Value, mode: &str) -> Value {
    let hi = if mode == "dayahead_vs_ml" && !item["cost"].is_null() && field(item, "cost") != 0. {
        field(item, "da_price") - field(item, "cost")
    } else {
        field(item, "price_diff")
    };
    let dev = field(item, "dev");
    json!(if dev == 0. {
        "平价平量"
    } else {
        TYPES[if hi >= 0. {
            if dev < 0. {
                0
            } else {
                1
            }
        } else if dev < 0. {
            2
        } else {
            3
        }]
    })
}
fn fmt(x: f64) -> String {
    let raw = format!("{:.2}", x);
    let (a, b) = raw.split_once('.').unwrap();
    let (sign, digits) = if a.starts_with('-') {
        ("-", &a[1..])
    } else {
        ("", a)
    };
    let mut out = String::from(sign);
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    format!("{}.{}", out, b)
}
fn offer(card: &Value, mwh: f64, price: f64) -> Result<Value> {
    if card.is_null() {
        return Ok(
            json!({"range":{"lower":null,"upper":null,"label":"-","usable":false},"evaluation":[null,null],"direction":null}),
        );
    }
    if mwh.abs() <= 0.05 {
        return Ok(
            json!({"range":{"lower":0.,"upper":0.,"label":"0","usable":true},"evaluation":[0.,"停机"],"direction":"停机"}),
        );
    }
    if 1. - field(card, "plant_rate") == 0. {
        return Err("float division by zero".into());
    }
    let output = mwh / (1. - field(card, "plant_rate"));
    let segments = list(&card["segments"]);
    if segments.is_empty() {
        return Ok(
            json!({"range":{"lower":null,"upper":null,"label":"-","usable":false},"evaluation":[output,"无报价单"],"direction":"-"}),
        );
    }
    let mut lower = field(&segments[0], "start");
    let mut upper: Option<f64> = None;
    let mut failures = Vec::new();
    let mut direction = "正常";
    if output + 0.05 < lower {
        failures.push(format!("机端应≥{}MW", fmt(lower)));
        direction = "偏低";
    }
    for s in segments {
        let p = field(s, "price");
        let start = field(s, "start");
        let end = field(s, "end");
        if price < p {
            upper = Some(upper.map_or(start, |u| u.min(start)));
        } else if (price - p).abs() <= 0.05 {
            lower = lower.max(start);
            upper = Some(upper.map_or(end, |u| u.min(end)));
        } else {
            lower = lower.max(end);
        }
        if price < p && output > start + 0.05 {
            failures.push(format!("价<{}时机组出力应≤{}MW", fmt(p), fmt(start)));
        } else if (price - p).abs() <= 0.05 && (output + 0.05 < start || output > end + 0.05) {
            failures.push(format!(
                "价={}时机组出力应在{}-{}MW",
                fmt(p),
                fmt(start),
                fmt(end)
            ));
        } else if price > p && output + 0.05 < end {
            failures.push(format!("价>{}时机组出力应≥{}MW", fmt(p), fmt(end)));
        }
        if direction == "正常" {
            if price < p && output > start + 0.05 {
                direction = "偏高";
            } else if (price - p).abs() <= 0.05 {
                if output + 0.05 < start {
                    direction = "偏低";
                } else if output > end + 0.05 {
                    direction = "偏高";
                }
            } else if price > p && output + 0.05 < end {
                direction = "偏低";
            }
        }
    }
    let usable = upper.is_none_or(|u| lower <= u + 0.05);
    let range = if !usable {
        "无有效范围".into()
    } else if let Some(u) = upper {
        format!("{}-{}MW", fmt(lower), fmt(u))
    } else {
        format!(">={}MW", fmt(lower))
    };
    let evaluation = if failures.is_empty() {
        "匹配".into()
    } else {
        format!("不匹配：{}", failures.join("；"))
    };
    Ok(
        json!({"range":{"lower":lower,"upper":upper,"label":range,"usable":usable},"evaluation":[output,evaluation],"direction":direction}),
    )
}
fn pair(s: &str) -> String {
    let normalized = s.replace(',', "、");
    match normalized.as_str() {
        "#1、" => "#1、#2".into(),
        "#3、" => "#3、#4".into(),
        "#5、" => "#5、#6".into(),
        "#7、" => "#7、#8".into(),
        _ => normalized,
    }
}
fn summary_parse_report(sheets: &Value) -> Result<Value> {
    let names = [
        "分公司火电",
        "汕头",
        "汕头#1机组",
        "汕头#2机组",
        "汕头#3机组",
        "海门",
        "海门#1机组",
        "海门#2机组",
        "海门#3机组",
        "海门#4机组",
        "东莞",
    ];
    let legacy = [4, 5, 8, 9, 10, 13, 16, 17, 18, 19, 21];
    let modern = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20];
    let brief_rows = [7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 19];
    let old = sheets.get("日前").is_some();
    let ws = if old {
        &sheets["日前"]
    } else {
        &sheets["新"]
    };
    if !old
        && !(matches!(cell(ws, 8, 1).as_str(), Some("火电" | "南方分公司"))
            && cell(ws, 8, 4) == "中长期"
            && cell(ws, 8, 7) == "日前出清")
    {
        return Err(format!(
            "未找到可识别的交易汇总页（旧版‘日前’或新版‘新’）。当前工作表：{}",
            sheets
                .as_object()
                .map(|o| o.keys().cloned().collect::<Vec<_>>().join("、"))
                .unwrap_or_default()
        )
        .into());
    }
    if !old && sheets.get("快报").is_none() {
        return Err("新版交易汇总页需要配套的‘快报’工作表。".into());
    }
    let mut rows = if old { legacy } else { modern };
    if !old && cell(ws, 8, 1) == "南方分公司" {
        let mut found = BTreeMap::new();
        let mut plant = String::new();
        for row in 10..61 {
            let l = label(cell(ws, row, 1)).trim().to_owned();
            let u = label(cell(ws, row, 2)).trim().to_owned();
            if ["新能源", "备注：", "备注:"].contains(&l.as_str()) {
                break;
            }
            let name = if l == "火电机组合计" {
                "分公司火电".into()
            } else if THERMAL.contains(&l.as_str()) {
                plant = l.clone();
                l
            } else if l.is_empty()
                && ["汕头", "海门"].contains(&plant.as_str())
                && ["#1机组", "#2机组", "#3机组", "#4机组"].contains(&u.as_str())
            {
                format!("{}{}", plant, u)
            } else {
                continue;
            };
            if found.insert(name.clone(), row).is_some() {
                return Err(format!("新版交易汇总页存在重复标签：{}", name).into());
            }
        }
        let mut missing = Vec::new();
        for (i, name) in names.iter().enumerate() {
            if let Some(row) = found.get(*name) {
                rows[i] = *row;
            } else {
                missing.push(*name);
            }
        }
        if !missing.is_empty() {
            missing.sort();
            return Err(format!("新版交易汇总页缺少标签：{}", missing.join("、")).into());
        }
    }
    let mut out = Map::new();
    for (i, name) in names.iter().enumerate() {
        let r = rows[i];
        let b = brief_rows[i];
        let brief = &sheets["快报"];
        out.insert(name.to_string(),if old{json!({"capacity_10mw":nv(cell(ws,r,3)),"cost":cell(ws,r,4),"load_rate":cell(ws,r,5),"da_mwh":n(cell(ws,r,6))*10.,"da_price":n(cell(ws,r,7)),"ml_mwh":n(cell(ws,r,8))*10.,"dev_mwh":n(cell(ws,r,9))*10.,"dev_price":nv(cell(ws,r,10)),"profit_wanyuan":n(cell(ws,r,11))})}else{json!({"capacity_10mw":nv(cell(brief,b,3)),"cost":cell(ws,r,3),"load_rate":nv(cell(brief,b,11)),"da_mwh":n(cell(ws,r,7))*10.,"da_price":n(cell(ws,r,8)),"ml_mwh":n(cell(ws,r,4))*10.,"dev_mwh":n(cell(ws,r,9))*10.,"dev_price":nv(cell(ws,r,10)),"profit_wanyuan":n(cell(ws,r,11))})});
    }
    Ok(Value::Object(out))
}
fn parse_offers(rows: &Value) -> Value {
    let mut cards = Map::new();
    for i in 1..=list(rows).len() {
        let label = cell(rows, i, 1);
        let Some(l) = label.as_str() else {
            continue;
        };
        let unit = if l.starts_with("东莞一套") {
            "东莞#1、#2机组"
        } else if l.starts_with("东莞二套") {
            "东莞#3、#4机组"
        } else if l.starts_with("东莞三套") {
            "东莞#5、#6机组"
        } else if l.starts_with("东莞四套") {
            "东莞#7、#8机组"
        } else if l.ends_with("机组") {
            l
        } else {
            continue;
        };
        if !THERMAL.iter().any(|p| unit.starts_with(p)) {
            continue;
        }
        let mut rate = Value::Null;
        let mut segments = Vec::new();
        for offset in 1..16 {
            if i + offset > list(rows).len() {
                break;
            }
            if cell(rows, i + offset, 17) == "厂用电率" {
                rate = nv(cell(rows, i + offset, 18));
            }
            if cell(rows, i + offset, 17) == "段号" {
                for r in i + offset + 1..=(i + offset + 10).min(i + 25).min(list(rows).len()) {
                    let (s, e, p) = (
                        nv(cell(rows, r, 18)),
                        nv(cell(rows, r, 19)),
                        nv(cell(rows, r, 20)),
                    );
                    if !s.is_null() && !e.is_null() && !p.is_null() {
                        segments.push(json!({"start":s,"end":e,"price":p}));
                    }
                }
                break;
            }
        }
        if !rate.is_null() {
            cards.insert(
                unit.into(),
                json!({"plant_rate":rate,"segments":merge(&json!(segments))}),
            );
        }
    }
    Value::Object(cards)
}
fn point(
    time: Value,
    ml: f64,
    ml_price: f64,
    da: f64,
    da_price: f64,
    rt: f64,
    rt_price: f64,
    cost: Option<f64>,
    dev: Option<f64>,
    horizontal: bool,
) -> Value {
    let d = dev.unwrap_or(da - ml);
    let diff = if horizontal { 0. } else { da_price - ml_price };
    let profit = if horizontal {
        0.
    } else {
        d * (da_price - cost.unwrap_or(ml_price)) / 10000.
    };
    let mut v = json!({"time":time,"ml":ml,"ml_price":ml_price,"da":da,"da_price":da_price,"rt":rt,"rt_price":rt_price,"dev_ml":if horizontal{0.}else{d},"price_diff_ml":diff,"profit_ml":profit});
    if let Some(c) = cost {
        v["cost"] = json!(c);
    }
    v
}
fn parse_blocks(rows: &Value, company: &str) -> Value {
    let mut blocks = Map::new();
    let mut order: Vec<String> = Vec::new();
    if (1..22).any(|c| {
        cell(rows, 2, c)
            .as_str()
            .is_some_and(|s| s.contains("实时电量"))
    }) {
        for c in (2..18).step_by(4) {
            let Some(l) = cell(rows, 1, c).as_str() else {
                continue;
            };
            if l.contains("合计") {
                continue;
            }
            let clean = l.trim();
            let unit = if clean.starts_with('#') {
                format!("{}{}机组", company, pair(&clean.replace("机组", "")))
            } else {
                format!("{}#{}机组", company, clean.chars().next().unwrap_or(' '))
            };
            let data: Vec<_> = (3..27)
                .map(|r| {
                    point(
                        json!(if cell(rows, r, 1)["__toolbox_cell_type"] == "time" {
                            format!("{}:00", cell(rows, r, 1)["hour"].as_u64().unwrap_or(0))
                        } else {
                            text(cell(rows, r, 1))
                        }),
                        0.,
                        0.,
                        n(cell(rows, r, c)),
                        n(cell(rows, r, c + 1)),
                        n(cell(rows, r, c + 2)),
                        n(cell(rows, r, c + 3)),
                        Some(0.),
                        Some(0.),
                        true,
                    )
                })
                .collect();
            if !blocks.contains_key(&unit) {
                order.push(unit.clone());
            }
            blocks.insert(unit, json!(data));
        }
    }
    for i in 1..=list(rows).len().saturating_sub(30) {
        let Some(l) = cell(rows, i, 1).as_str() else {
            continue;
        };
        let clean = l.trim();
        if !(["1号机", "2号机", "3号机", "4号机"].contains(&clean)
            || (clean.starts_with('#') && clean.contains("机组")))
        {
            continue;
        }
        let mut data = Vec::new();
        for off in 3..27 {
            let r = i + off;
            let time = cell(rows, r, 1);
            if !time.as_str().is_some_and(|s| s.contains(':')) {
                break;
            }
            data.push(point(
                time.clone(),
                n(cell(rows, r, 2)),
                n(cell(rows, r, 3)),
                n(cell(rows, r, 5)),
                n(cell(rows, r, 6)),
                n(cell(rows, r, 7)),
                n(cell(rows, r, 8)),
                Some(n(cell(rows, r, 10))),
                cell(rows, r, 11).as_f64(),
                false,
            ));
        }
        if data.len() == 24 {
            let unit = if clean.ends_with("号机") {
                format!("{}#{}机组", company, clean.chars().next().unwrap())
            } else {
                format!("{}{}", company, clean)
            };
            if !blocks.contains_key(&unit) {
                order.push(unit.clone());
            }
            blocks.insert(unit, json!(data));
        }
    }
    json!({"hourly":blocks,"unit_order":order})
}
fn aggregate(data: &Value) -> Value {
    let sum = |key: &str| py_sum(list(data).iter().map(|p| field(p, key)));
    let (da, rt, ml) = (sum("da"), sum("rt"), sum("ml"));
    let avg = |q: &str, p: &str, total: f64| {
        if total == 0. {
            0.
        } else {
            py_sum(list(data).iter().map(|v| field(v, q) * field(v, p))) / total
        }
    };
    let profit = sum("profit_ml");
    let profit_rt = py_sum(list(data).iter().map(|p| {
        (field(p, "rt") - field(p, "da")) * (field(p, "rt_price") - field(p, "da_price")) / 10000.
    }));
    json!({"da_mwh":da,"da_price":avg("da","da_price",da),"rt_mwh":rt,"rt_price_avg":avg("rt","rt_price",rt),"ml_mwh":ml,"ml_price_avg":avg("ml","ml_price",ml),"dev_mwh":da-ml,"dev_ml_mwh":da-ml,"profit_wanyuan":profit,"profit_ml_wanyuan":profit,"dev_rt_mwh":rt-da,"profit_rt_wanyuan":profit_rt,"load_rate":null})
}
fn parse_renewables(sheets: &Value) -> (Value, Value) {
    let mut hourly = Map::new();
    let mut summaries = Map::new();
    for unit in RENEWABLE {
        let rows = &sheets[unit];
        let mut data = Vec::new();
        for r in 4..28 {
            let t = cell(rows, r, 1);
            if !t.as_str().is_some_and(|s| s.contains(':')) {
                continue;
            }
            data.push(point(
                t.clone(),
                n(cell(rows, r, 12)),
                n(cell(rows, r, 13)),
                n(cell(rows, r, 2)),
                n(cell(rows, r, 3)),
                n(cell(rows, r, 4)),
                n(cell(rows, r, 5)),
                None,
                None,
                false,
            ));
        }
        let data = json!(data);
        let mut summary = aggregate(&data);
        summary["full_price"] = nv(cell(rows, 28, 31));
        hourly.insert(unit.into(), data);
        summaries.insert(unit.into(), summary);
    }
    (Value::Object(hourly), Value::Object(summaries))
}
fn report(payload: &Value) -> Result<Value> {
    let sheets = &payload["sheets"];
    let mut summary = summary_parse_report(sheets)?;
    let cards = parse_offers(&sheets["运行补偿"]);
    for name in ["运行补偿", "汕头", "海门", "东莞", "海风", "鮀莲", "归湖"] {
        if sheets.get(name).is_none() {
            return Err(format!("Worksheet {} does not exist.", name).into());
        }
    }
    let mut hourly = Map::new();
    let mut unit_order = Vec::new();
    for plant in THERMAL {
        let blocks = parse_blocks(&sheets[plant], plant);
        hourly.extend(blocks["hourly"].as_object().unwrap().clone());
        unit_order.extend(list(&blocks["unit_order"]).iter().cloned());
    }
    unit_order.extend(RENEWABLE.iter().map(|u| json!(u)));
    let (rh, rs) = parse_renewables(sheets);
    hourly.extend(rh.as_object().unwrap().clone());
    summary
        .as_object_mut()
        .unwrap()
        .extend(rs.as_object().unwrap().clone());
    let mode = if hourly
        .values()
        .flat_map(list)
        .any(|p| field(p, "rt").abs() > 1e-6 || field(p, "rt_price").abs() > 1e-6)
    {
        "realtime_vs_dayahead"
    } else {
        "dayahead_vs_ml"
    };
    for units in payload["companies"]
        .as_object()
        .into_iter()
        .flat_map(|m| m.values())
    {
        for unit in list(units) {
            let Some(name) = unit.as_str() else {
                continue;
            };
            if RENEWABLE.contains(&name) {
                continue;
            }
            if let Some(data) = hourly.get(name) {
                let agg = aggregate(data);
                let mut base = summary[name].as_object().cloned().unwrap_or_default();
                base.extend(agg.as_object().unwrap().clone());
                summary[name] = Value::Object(base);
            }
        }
    }
    Ok(
        json!({"summary":summary,"hourly":hourly,"offer_cards":cards,"compare_mode":mode,"unit_order":unit_order}),
    )
}
fn numeric(cell: &Value) -> Result<Value> {
    let v = &cell["value"];
    let mut format = cell["number_format"]
        .as_str()
        .filter(|s| !s.is_empty())
        .unwrap_or("General")
        .to_string();
    let value = if let Some(x) = v.as_f64() {
        x
    } else if let Some(b) = v.as_bool() {
        if b {
            1.
        } else {
            0.
        }
    } else {
        let s = v.as_str().unwrap_or("").replace(',', "").trim().to_string();
        if s.is_empty() {
            return Err("数值为空".into());
        }
        if format == "General" {
            if let Some((_, decimal)) = s.split_once('.') {
                if !decimal.is_empty() && decimal.chars().all(|c| c.is_ascii_digit()) {
                    format = format!("0.{}", "0".repeat(decimal.len()));
                }
            }
        }
        s.parse::<f64>()?
    };
    if !value.is_finite() {
        return Err("数值必须有限".into());
    }
    Ok(json!({"value":value,"number_format":format}))
}
fn summary_parse(payload: &Value) -> Result<Value> {
    let mut out = Vec::new();
    for sheet in list(&payload["sheets"]) {
        let rows = &sheet["rows"];
        let header = list(rows).first().map(list).unwrap_or(&[]);
        let find = |keyword: &str| {
            header
                .iter()
                .position(|c| label(&c["value"]).trim().contains(keyword))
        };
        let (Some(tc), Some(ec), Some(pc)) = (
            find("时刻"),
            find("电量"),
            find("结算电价").or_else(|| find("出清电价")),
        ) else {
            continue;
        };
        let mut by_hour: BTreeMap<String, (Value, Value)> = BTreeMap::new();
        for row in list(rows).iter().skip(1) {
            let raw = label(&row.get(tc).unwrap_or(&Value::Null)["value"])
                .trim()
                .to_string();
            let slot = if let Some((h, m)) = raw.split_once(':') {
                if !h.is_empty() && h.chars().all(|c| c.is_ascii_digit()) {
                    format!(
                        "{:02}:{}",
                        h.parse::<u32>()?,
                        m.chars().take(2).collect::<String>()
                    )
                } else {
                    raw
                }
            } else {
                raw
            };
            if !(0..24).any(|h| slot == format!("{:02}:00", h)) {
                continue;
            }
            let energy = numeric(row.get(ec).unwrap_or(&Value::Null));
            let price = numeric(row.get(pc).unwrap_or(&Value::Null));
            match (energy, price) {
                (Ok(e), Ok(p)) => {
                    by_hour.insert(slot, (e, p));
                }
                _ => {
                    return Err(format!(
                        "{} / {} / {}：电量或电价缺失或无效",
                        label(&payload["filename"]),
                        label(&sheet["name"]),
                        slot
                    )
                    .into())
                }
            }
        }
        let missing: Vec<_> = (0..24)
            .map(|h| format!("{:02}:00", h))
            .filter(|h| !by_hour.contains_key(h))
            .collect();
        if !missing.is_empty() {
            return Err(format!(
                "{} / {}：缺少时段 {}",
                label(&payload["filename"]),
                label(&sheet["name"]),
                missing.join(", ")
            )
            .into());
        }
        let title = label(&sheet["name"]);
        // Match the first #digits marker anywhere, as Python re.search does;
        // descriptive # characters before the unit marker are valid titles.
        let digits = title.split('#').skip(1).find_map(|tail| {
            let digits: String = tail.chars().take_while(|c| c.is_ascii_digit()).collect();
            if digits.is_empty() {
                None
            } else {
                Some(digits)
            }
        });
        let unit = if let Some(digits) = digits {
            let normalized = digits.trim_start_matches('0');
            format!(
                "{}号机",
                if normalized.is_empty() {
                    "0"
                } else {
                    normalized
                }
            )
        } else {
            title.trim().to_string()
        };
        out.push(json!({"unit_name":unit,"energy":by_hour.values().map(|p|p.0.clone()).collect::<Vec<_>>(),"price":by_hour.values().map(|p|p.1.clone()).collect::<Vec<_>>()}));
    }
    Ok(json!(out))
}
pub fn execute(payload: &Value) -> Result<Value> {
    COMPENSATED_SUM.with(|mode| mode.set(payload["_compensated_sum"].as_bool().unwrap_or(true)));
    match payload["action"].as_str().unwrap_or("") {
        "offer_merge" => Ok(merge(&payload["segments"])),
        "classify" => Ok(classify(
            &payload["item"],
            payload["compare_mode"].as_str().unwrap_or(""),
        )),
        "offers" => list(&payload["points"])
            .iter()
            .map(|p| offer(&payload["card"], n(&p[0]), n(&p[1])))
            .collect::<Result<Vec<_>>>()
            .map(|v| json!(v)),
        "fill_summary" => Ok(fill_summary(payload)),
        "company_summary" => company_summary(payload),
        "representative" => Ok(representative(payload)),
        "unit_order" => Ok(unit_order(payload)),
        "rank" => Ok(rank(payload)),
        "start_stop" => Ok(json!(start_stop(list(&payload["rows"])))),
        "deviations" => deviations(payload),
        "offer" => offer(
            &payload["card"],
            field(payload, "clearing_mwh"),
            field(payload, "clearing_price"),
        ),
        "report" => report(payload),
        "report_summary" => summary_parse_report(&payload["sheets"]).map_err(|error| {
            if let Some((prefix, _)) = error.to_string().split_once("当前工作表：") {
                if let Some(names) = payload["available_sheets"].as_array() {
                    return format!(
                        "{}当前工作表：{}",
                        prefix,
                        names.iter().map(label).collect::<Vec<_>>().join("、")
                    )
                    .into();
                }
            }
            error
        }),
        "report_offers" => Ok(parse_offers(&payload["rows"])),
        "report_blocks" => Ok(parse_blocks(
            &payload["rows"],
            payload["company"].as_str().unwrap_or(""),
        )),
        "report_renewables" => {
            let (h, s) = parse_renewables(&payload["sheets"]);
            Ok(json!({"hourly":h,"summary":s}))
        }
        "aggregate" => Ok(aggregate(&payload["points"])),
        "summary_parse" => summary_parse(payload),
        other => Err(format!("unknown trading action: {}", other).into()),
    }
}
fn fill_summary(payload: &Value) -> Value {
    let mut summary = payload["summary"].clone();
    for units in payload["companies"]
        .as_object()
        .into_iter()
        .flat_map(|m| m.values())
    {
        for unit in list(units) {
            let Some(name) = unit.as_str() else {
                continue;
            };
            if RENEWABLE.contains(&name) {
                continue;
            }
            if let Some(data) = payload["hourly"].get(name) {
                let mut base = summary[name].as_object().cloned().unwrap_or_default();
                base.extend(aggregate(data).as_object().unwrap().clone());
                summary[name] = Value::Object(base);
            }
        }
    }
    summary
}
fn company_summary(payload: &Value) -> Result<Value> {
    let summary = &payload["summary"];
    let company = payload["company"].as_str().unwrap_or("");
    if payload["compare_mode"] == "dayahead_vs_ml" && summary.get(company).is_some() {
        let mut item = summary[company].clone();
        // Python evaluates dict.get's default eagerly: preserve null errors.
        if item.get("dev_price").is_some_and(Value::is_null) {
            return Err("unsupported operand type(s) for +: 'NoneType' and number".into());
        }
        let defaults = [
            (
                "ml_price_avg",
                field(&item, "dev_price") + field(&item, "da_price"),
            ),
            ("dev_ml_mwh", field(&item, "dev_mwh")),
            ("profit_ml_wanyuan", field(&item, "profit_wanyuan")),
            ("rt_mwh", 0.),
            ("rt_price_avg", 0.),
            ("profit_rt_wanyuan", 0.),
        ];
        for (k, v) in defaults {
            if item.get(k).is_none() {
                item[k] = json!(v);
            }
        }
        if item.get("dev_rt_mwh").is_none() {
            item["dev_rt_mwh"] = json!(field(&item, "rt_mwh") - field(&item, "da_mwh"));
        }
        return Ok(item);
    }
    let units = list(&payload["units"]);
    let sum = |key: &str| {
        py_sum(
            units
                .iter()
                .map(|u| field(&summary[u.as_str().unwrap_or("")], key)),
        )
    };
    let (da, rt, ml) = (sum("da_mwh"), sum("rt_mwh"), sum("ml_mwh"));
    let avg = |q: &str, p: &str, total: f64| {
        if total == 0. {
            0.
        } else {
            py_sum(units.iter().map(|u| {
                let s = &summary[u.as_str().unwrap_or("")];
                field(s, q) * field(s, p)
            })) / total
        }
    };
    let profit_ml = py_sum(units.iter().map(|u| {
        let s = &summary[u.as_str().unwrap_or("")];
        s.get("profit_ml_wanyuan")
            .map(n)
            .unwrap_or_else(|| field(s, "profit_wanyuan"))
    }));
    let mut item = json!({"da_mwh":da,"da_price":avg("da_mwh","da_price",da),"rt_mwh":rt,"rt_price_avg":avg("rt_mwh","rt_price_avg",rt),"ml_mwh":ml,"ml_price_avg":avg("ml_mwh","ml_price_avg",ml),"dev_ml_mwh":da-ml,"profit_ml_wanyuan":profit_ml,"dev_rt_mwh":rt-da,"profit_rt_wanyuan":sum("profit_rt_wanyuan")});
    if summary.get(company).is_some() {
        item["full_price"] = summary[company]["full_price"].clone();
    }
    Ok(item)
}
fn representative(payload: &Value) -> Value {
    let mut points = Vec::new();
    let realtime = payload["compare_mode"] == "realtime_vs_dayahead";
    let mode = payload["compare_mode"].as_str().unwrap_or("");
    for unit in list(&payload["units"]) {
        for point in list(&payload["hourly"][unit.as_str().unwrap_or("")]) {
            let mut p = point.clone();
            p["unit"] = unit.clone();
            let dev = if realtime {
                field(&p, "rt") - field(&p, "da")
            } else {
                field(&p, "da") - field(&p, "ml")
            };
            let diff = if realtime {
                field(&p, "rt_price") - field(&p, "da_price")
            } else {
                field(&p, "da_price") - field(&p, "ml_price")
            };
            let profit =
                dev * if !realtime && !p["cost"].is_null() && field(&p, "cost") != 0. {
                    field(&p, "da_price") - field(&p, "cost")
                } else {
                    diff
                } / 10000.;
            p["dev"] = json!(dev);
            p["price_diff"] = json!(diff);
            p["profit"] = json!(profit);
            p["type"] = classify(&p, mode);
            points.push(p);
        }
    }
    let limit = payload["limit"].as_i64().unwrap_or(5);
    let mut results = Vec::new();
    for category in TYPES {
        let mut chosen: Vec<_> = points
            .iter()
            .filter(|p| p["type"] == category)
            .cloned()
            .collect();
        chosen.sort_by(|a, b| {
            field(b, "profit")
                .abs()
                .total_cmp(&field(a, "profit").abs())
        });
        let take = if limit < 0 {
            chosen.len().saturating_sub((-limit) as usize)
        } else {
            limit as usize
        };
        results.extend(chosen.into_iter().take(take));
    }
    let hour = |v: &Value| {
        label(&v["time"])
            .split(':')
            .next()
            .unwrap_or("")
            .parse::<i64>()
            .unwrap_or(0)
    };
    results.sort_by(|a, b| {
        label(&a["unit"])
            .cmp(&label(&b["unit"]))
            .then_with(|| hour(a).cmp(&hour(b)))
    });
    json!(results)
}
fn unit_order(payload: &Value) -> Value {
    let units = list(&payload["units"]);
    let station = list(&payload["station_order"]);
    let mut indices: Vec<usize> = (0..units.len()).collect();
    let key = |i: usize| {
        let u = &units[i];
        let name = label(&u["unit_name"]);
        let rank = name
            .strip_suffix("号机")
            .and_then(|s| {
                if !s.is_empty() && s.chars().all(|c| c.is_ascii_digit()) {
                    s.parse::<u64>().ok()
                } else {
                    None
                }
            })
            .unwrap_or(9999);
        (
            station
                .iter()
                .position(|s| s == &u["station_key"])
                .unwrap_or(9999),
            rank,
            name,
        )
    };
    indices.sort_by_key(|i| key(*i));
    json!(indices)
}
fn start_stop(rows: &[Value]) -> bool {
    let mut statuses: BTreeMap<String, (bool, bool)> = BTreeMap::new();
    for row in rows {
        let status = statuses.entry(label(&row["unit"])).or_default();
        if field(row, "clearing_mwh").abs() <= 0.05 {
            status.0 = true;
        } else {
            status.1 = true;
        }
    }
    statuses.values().any(|(a, b)| *a && *b)
}
fn rank(payload: &Value) -> Value {
    let rows = list(&payload["rows"]);
    let mut dates: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    for row in rows {
        dates
            .entry(label(&row["date"]))
            .or_default()
            .push(row.clone());
    }
    let excluded: BTreeSet<_> = dates
        .iter()
        .filter(|(_, rs)| start_stop(rs))
        .map(|(d, _)| d.clone())
        .collect();
    let mut grouped: Vec<((String, String), Vec<Value>)> = Vec::new();
    for row in rows {
        let key = (label(&row["market_type"]), label(&row["date"]));
        if let Some((_, rs)) = grouped.iter_mut().find(|(k, _)| *k == key) {
            rs.push(row.clone());
        } else {
            grouped.push((key, vec![row.clone()]));
        }
    }
    let mut scores = Vec::new();
    for ((market, date), rs) in grouped {
        if excluded.contains(&date) {
            continue;
        }
        let total = py_sum(rs.iter().map(|r| field(r, "deviation_mw")));
        let rounded = format!("{:.6}", total).parse::<f64>().unwrap();
        let count = rs
            .iter()
            .map(|r| label(&r["unit"]))
            .collect::<BTreeSet<_>>()
            .len();
        scores.push(json!({"market_type":market,"date":date,"total_deviation_mw":rounded,"unit_count":count,"point_count":rs.len(),"source_file":rs[0]["source_file"]}));
    }
    scores
        .sort_by(|a, b| field(b, "total_deviation_mw").total_cmp(&field(a, "total_deviation_mw")));
    let mut limited = Vec::new();
    let top = payload["top"].as_i64().unwrap_or(10);
    for market in ["日前", "实时"] {
        let matches: Vec<_> = scores
            .iter()
            .filter(|s| s["market_type"] == market)
            .cloned()
            .collect();
        let take = if top < 0 {
            matches.len().saturating_sub((-top) as usize)
        } else {
            top as usize
        };
        limited.extend(matches.into_iter().take(take));
    }
    json!({"scores":limited,"excluded":excluded.into_iter().map(|d|json!([d,"存在机组出力状态变化"])).collect::<Vec<_>>()})
}
fn deviations(payload: &Value) -> Result<Value> {
    let mut rows = Vec::new();
    let filters = list(&payload["unit_filters"]);
    let order = list(&payload["unit_order"]);
    for unit in order {
        let name = unit.as_str().unwrap_or("");
        if !filters.is_empty()
            && !filters
                .iter()
                .any(|f| name.contains(f.as_str().unwrap_or("")))
        {
            continue;
        }
        let card = &payload["offer_cards"][name];
        if card.is_null() {
            continue;
        }
        for point in list(&payload["hourly"][name]) {
            for (market, q, p) in [("日前", "da", "da_price"), ("实时", "rt", "rt_price")] {
                let mwh = field(point, q);
                let price = field(point, p);
                if market == "实时" && mwh.abs() <= 1e-6 && price.abs() <= 1e-6 {
                    continue;
                }
                let result = offer(card, mwh, price)?;
                let normal = &result["range"];
                if normal["usable"] != true {
                    continue;
                }
                let output = if mwh.abs() <= 0.05 {
                    0.
                } else {
                    mwh / (1. - field(card, "plant_rate"))
                };
                let distance = if !normal["lower"].is_null() && output < field(normal, "lower") {
                    field(normal, "lower") - output
                } else if !normal["upper"].is_null() && output > field(normal, "upper") {
                    output - field(normal, "upper")
                } else {
                    0.
                };
                rows.push(json!({"market_type":market,"date":payload["date"],"unit":name,"time":text(&point["time"]),"clearing_mwh":mwh,"clearing_price":price,"plant_output":output,"normal_range":normal["label"],"deviation_mw":distance,"source_file":payload["source_file"]}));
            }
        }
    }
    Ok(json!(rows))
}
