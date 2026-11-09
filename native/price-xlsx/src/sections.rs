use bigdecimal::{BigDecimal, RoundingMode, ToPrimitive};
use regex::Regex;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::str::FromStr;
use std::sync::{Mutex, OnceLock};
fn regex(pattern: &str) -> Regex {
    static CACHE: OnceLock<Mutex<HashMap<String, Regex>>> = OnceLock::new();
    let mut cache = CACHE
        .get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .unwrap();
    cache
        .entry(pattern.to_string())
        .or_insert_with(|| Regex::new(pattern).unwrap())
        .clone()
}
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
fn text(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        _ => v.to_string(),
    }
}
fn s(v: &Value) -> String {
    text(v).trim().to_string()
}
fn sub(pattern: &str, replacement: &str, input: &str) -> String {
    regex(pattern).replace_all(input, replacement).into_owned()
}
fn price_name(v: &Value) -> String {
    let mut t = s(v);
    t = sub(r"^其他", "", &t);
    for x in ["开关站", "换流站", "站", "变"] {
        t = t.replace(x, "");
    }
    for p in [
        r"(?i)\(500kV\)",
        r"(?i)500\s*kV.*$",
        r"(?i)220\s*kV.*$",
        r"#.*$",
        r"\d+母$",
    ] {
        t = sub(p, "", &t);
    }
    t.trim().into()
}
fn node_key(v: &Value) -> String {
    let t = price_name(v).trim_end_matches(['.', '。', ' ']).to_string();
    sub(r"电厂$", "厂", &sub(r"核电厂$", "核电", &t))
}
fn node_name(v: &Value) -> String {
    let t = sub(r"\s*#\d+[A-Za-z]?\s*$", "", &s(v));
    let t = sub(
        r"(?i)((?:500|220)\s*[_-]?\s*k\s*v)\d+[A-Za-z]?\s*$",
        "$1",
        t.trim(),
    );
    sub(r"(?i)(500|220)\s*[_-]?\s*k\s*v", "${1}kV", t.trim())
        .trim()
        .into()
}
fn topology(t: &str) -> Result<Value> {
    let mut active = false;
    let mut declared_active = false;
    let mut group = String::new();
    let mut edges = vec![];
    let mut duplicates = vec![];
    let mut seen = HashSet::new();
    let mut declared = BTreeSet::new();
    let mut connected = BTreeSet::new();
    for raw in t.lines() {
        let l = raw.trim();
        if regex(r"^##\s+1\.").is_match(l) {
            declared_active = true;
        } else if regex(r"^##\s+2\.").is_match(l) {
            declared_active = false;
        } else if declared_active && !l.is_empty() && !l.starts_with('#') {
            for n in regex("[、，,]").split(l) {
                if !n.trim().is_empty() {
                    declared.insert(n.trim().to_string());
                }
            }
        }
        if !active {
            active = regex(r"^##\s+2\.").is_match(l);
            continue;
        }
        if regex(r"^##\s+3\.").is_match(l) {
            break;
        }
        if let Some(g) = l.strip_prefix("### ") {
            group = g.trim().into();
            continue;
        }
        if l.is_empty() || group.is_empty() {
            continue;
        }
        let parts: Vec<String> = if let Some((a, b)) = l.split_once('：') {
            regex("[，,、]")
                .split(b)
                .filter(|n| !n.trim().is_empty())
                .map(|n| format!("{}-{}", a.trim(), n.trim()))
                .collect()
        } else {
            regex("[，,、]").split(l).map(str::to_string).collect()
        };
        for part in parts {
            if let Some((a, b)) = part.trim().split_once('-') {
                let (a, b) = (a.trim().to_string(), b.trim().to_string());
                let key = if a < b {
                    (a.clone(), b.clone())
                } else {
                    (b.clone(), a.clone())
                };
                if !seen.insert(key) {
                    duplicates.push(json!([a, b, group]));
                } else {
                    connected.insert(a.clone());
                    connected.insert(b.clone());
                    edges.push(json!({"group":group,"a":a,"b":b}));
                }
            }
        }
    }
    if edges.is_empty() {
        return Err("拓扑文件未解析到“## 2.”下的边".into());
    }
    let nodes: BTreeSet<_> = connected.union(&declared).cloned().collect();
    Ok(json!({"edges":edges,"duplicates":duplicates,"nodes":nodes,"connected":connected}))
}
/// Same exact-normalized/longest-contained precedence as the Python index.
struct TopologyMatcher {
    ordered: Vec<String>,
    exact: HashMap<String, String>,
    cache: HashMap<String, Option<String>>,
}
impl TopologyMatcher {
    fn new(nodes: &[Value]) -> Self {
        let mut ordered: Vec<_> = nodes.iter().map(s).collect();
        ordered.sort_by(|a, b| b.chars().count().cmp(&a.chars().count()).then(a.cmp(b)));
        let mut exact = HashMap::new();
        for node in &ordered {
            exact
                .entry(price_name(&json!(node)))
                .or_insert_with(|| node.clone());
        }
        Self {
            ordered,
            exact,
            cache: HashMap::new(),
        }
    }
    fn matching(&mut self, value: &Value) -> Option<String> {
        let raw = s(value);
        if let Some(found) = self.cache.get(&raw) {
            return found.clone();
        }
        let found = self.exact.get(&price_name(value)).cloned().or_else(|| {
            self.ordered
                .iter()
                .find(|node| raw.contains(node.as_str()))
                .cloned()
        });
        self.cache.insert(raw, found.clone());
        found
    }
}
fn decimal(v: &Value) -> Option<BigDecimal> {
    BigDecimal::from_str(&s(v)).ok().map(|d| {
        d.with_precision_round(
            std::num::NonZeroU64::new(28).unwrap(),
            RoundingMode::HalfEven,
        )
    })
}
fn key(v: &Value) -> String {
    if v.is_null() {
        return "s:".into();
    }
    if let Some(d) = decimal(v) {
        format!("n:{}", d.normalized())
    } else {
        format!("s:{}", s(v))
    }
}
fn mean(rows: &[Value]) -> Result<Value> {
    if rows.len() == 1 {
        return Ok(rows[0].clone());
    }
    let width = rows
        .iter()
        .map(|v| v.as_array().unwrap().len())
        .min()
        .unwrap_or(0);
    let mut out = vec![];
    for i in 0..width {
        let mut sum = BigDecimal::from(0);
        let mut count = 0;
        for r in rows {
            let v = &r[i];
            if v.is_null() || s(v).is_empty() {
                continue;
            }
            let d =
                decimal(v).ok_or_else(|| format!("同一拓扑节点存在无法求平均的分时价格: {v}"))?;
            sum = (sum + d).with_precision_round(
                std::num::NonZeroU64::new(28).unwrap(),
                RoundingMode::HalfEven,
            );
            count += 1;
        }
        out.push(if count == 0 {
            Value::Null
        } else {
            json!((sum / BigDecimal::from(count))
                .with_precision_round(
                    std::num::NonZeroU64::new(28).unwrap(),
                    RoundingMode::HalfEven
                )
                .to_f64()
                .ok_or("decimal out of float range")?)
        });
    }
    Ok(json!(out))
}
fn dedupe(p: &Value) -> Result<Value> {
    let mut matcher = p["nodes"]
        .as_array()
        .map(|nodes| TopologyMatcher::new(nodes));
    let mut seen = HashSet::new();
    let mut groups: Vec<(String, Value, Vec<Value>)> = vec![];
    let mut group_keys: BTreeMap<(String, String), usize> = BTreeMap::new();
    let mut sheets = vec![];
    let mut kept = 0;
    let mut removed = 0;
    let mut headers: Option<Value> = None;
    let mut title = String::new();
    for sheet in p["sheets"].as_array().ok_or("missing sheets")? {
        let mut output = vec![];
        for row in sheet["rows"].as_array().ok_or("missing rows")? {
            let name = &row[0];
            let item = &row[1];
            let prices = json!(row.as_array().unwrap()[2..]);
            if let Some(matcher) = &mut matcher {
                let Some(n) = matcher.matching(name) else {
                    removed += 1;
                    continue;
                };
                if let Some(h) = &headers {
                    if h != &sheet["headers"] {
                        return Err("包含拓扑节点的工作表分时时间列不一致，无法合并".into());
                    }
                } else {
                    headers = Some(sheet["headers"].clone());
                    title = s(&sheet["name"]);
                }
                let k = (n.clone(), key(item));
                if let Some(&i) = group_keys.get(&k) {
                    groups[i].2.push(prices);
                    removed += 1;
                } else {
                    group_keys.insert(k, groups.len());
                    groups.push((n, item.clone(), vec![prices]));
                    kept += 1;
                }
            } else {
                if !regex(r"(?i)500\s*[_-]?\s*k\s*v").is_match(&text(name)) {
                    removed += 1;
                    continue;
                }
                let k: Vec<_> = row.as_array().unwrap()[1..].iter().map(key).collect();
                if !seen.insert(k) {
                    removed += 1;
                    continue;
                }
                let mut r = row.as_array().unwrap().clone();
                r[0] = json!(node_name(name));
                output.push(json!(r));
                kept += 1;
            }
        }
        if !output.is_empty() {
            sheets.push(json!({"name":sheet["name"],"headers":sheet["headers"],"rows":output}));
        }
    }
    if !groups.is_empty() {
        let mut rows = vec![];
        for (n, item, prices) in groups {
            let mut row = vec![json!(n), item];
            row.extend(mean(&prices)?.as_array().unwrap().clone());
            rows.push(json!(row));
        }
        sheets.push(json!({"name":title,"headers":headers,"rows":rows}));
    }
    Ok(json!({"sheets":sheets,"kept":kept,"removed":removed}))
}
fn matched(nodes: &[Value], rows: &[Value]) -> BTreeMap<String, Value> {
    let mut out = BTreeMap::new();
    for n in nodes {
        let n = s(n);
        let mut candidates: Vec<_> = rows
            .iter()
            .filter(|r| s(&r["normalized_name"]) == n)
            .collect();
        if candidates.is_empty() {
            candidates = rows
                .iter()
                .filter(|r| s(&r["raw_name"]).contains(&n))
                .collect();
        }
        candidates.sort_by_key(|r| {
            let raw = s(&r["raw_name"]);
            let lower = raw.to_lowercase();
            (!(lower.contains("500kv") && !lower.contains("220kv")), raw)
        });
        if let Some(r) = candidates.first() {
            out.insert(n, (*r).clone());
        }
    }
    out
}
fn windows(records: &[Value], times: &[Value]) -> String {
    let index: BTreeMap<_, _> = times.iter().enumerate().map(|(i, t)| (s(t), i)).collect();
    let mut indexes: Vec<_> = records.iter().map(|r| index[&s(&r["time"])]).collect();
    indexes.sort();
    if indexes.is_empty() {
        return String::new();
    }
    let mut start = indexes[0];
    let mut prev = start;
    let mut result = vec![];
    let label = |a: usize, b: usize| {
        if a == b {
            s(&times[a])
        } else {
            format!("{}-{}", s(&times[a]), s(&times[b]))
        }
    };
    for i in indexes.into_iter().skip(1) {
        if i == prev + 1 {
            prev = i;
        } else {
            result.push(label(start, prev));
            start = i;
            prev = i;
        }
    }
    result.push(label(start, prev));
    result.join("; ")
}
fn summaries(records: &[Value], times: &[Value]) -> Value {
    let mut edges: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    let mut groups: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    for r in records {
        edges.entry(s(&r["edge"])).or_default().push(r.clone());
        groups.entry(s(&r["group"])).or_default().push(r.clone());
    }
    let mut er = vec![];
    for (edge, rs) in edges {
        let mut max = &rs[0];
        let mut counts: BTreeMap<String, usize> = BTreeMap::new();
        for r in &rs {
            if r["abs_diff"].as_f64().unwrap() > max["abs_diff"].as_f64().unwrap() {
                max = r;
            }
            *counts.entry(s(&r["direction"])).or_default() += 1;
        }
        let direction = counts.iter().max_by_key(|(d, c)| (**c, *d)).unwrap().0;
        let count_text = counts
            .iter()
            .map(|(d, c)| format!("{d}:{c}"))
            .collect::<Vec<_>>()
            .join("; ");
        er.push(json!({"group":max["group"],"edge":edge,"intervals":rs.len(),"windows":windows(&rs,times),"max_abs_diff":format!("{:.2}",max["abs_diff"].as_f64().unwrap()),"max_time":max["time"],"max_low":max["low"],"max_high":max["high"],"main_direction":direction,"direction_counts":count_text}));
    }
    let mut gr = vec![];
    for (g, rs) in groups {
        let max = rs
            .iter()
            .map(|r| r["abs_diff"].as_f64().unwrap())
            .fold(f64::NEG_INFINITY, f64::max);
        let es: BTreeSet<_> = rs.iter().map(|r| s(&r["edge"])).collect();
        gr.push(json!({"group":g,"flagged_edges":es.len(),"flagged_intervals":rs.len(),"max_abs_diff":format!("{max:.2}")}));
    }
    gr.sort_by(|a, b| {
        s(&b["max_abs_diff"])
            .parse::<f64>()
            .unwrap()
            .total_cmp(&s(&a["max_abs_diff"]).parse::<f64>().unwrap())
            .then(s(&a["group"]).cmp(&s(&b["group"])))
    });
    json!({"edge_summary":er,"group_summary":gr})
}
fn analyze(p: &Value) -> Result<Value> {
    let times = p["time_headers"].as_array().ok_or("missing time headers")?;
    let edges = p["edges"].as_array().ok_or("missing edges")?;
    let nodes: BTreeSet<String> = edges
        .iter()
        .flat_map(|e| [s(&e["a"]), s(&e["b"])])
        .collect();
    let nv: Vec<Value> = nodes.iter().map(|n| json!(n)).collect();
    let rows = matched(&nv, p["price_rows"].as_array().ok_or("missing price rows")?);
    let missing: Vec<_> = nodes.iter().filter(|n| !rows.contains_key(*n)).collect();
    let threshold = p["threshold"].as_f64().unwrap_or(80.);
    let mut records = vec![];
    for e in edges {
        let (a, b) = (s(&e["a"]), s(&e["b"]));
        let (Some(ar), Some(br)) = (rows.get(&a), rows.get(&b)) else {
            continue;
        };
        let mut rs = vec![];
        for i in 1..times.len() {
            let (t, prev) = (s(&times[i]), s(&times[i - 1]));
            let (Some(pa), Some(pb), Some(oa), Some(ob)) = (
                ar["prices"][&t].as_f64(),
                br["prices"][&t].as_f64(),
                ar["prices"][&prev].as_f64(),
                br["prices"][&prev].as_f64(),
            ) else {
                continue;
            };
            let (ma, mb) = (pa - oa, pb - ob);
            if ma * mb >= 0. {
                continue;
            }
            let diff = pb - pa;
            if diff.abs() <= threshold {
                continue;
            }
            let (low, high) = if diff > 0. { (&a, &b) } else { (&b, &a) };
            rs.push(json!({"group":e["group"],"edge":format!("{a}-{b}"),"a":a,"b":b,"time":t,"price_a":pa,"price_b":pb,"move_a":ma,"move_b":mb,"diff_b_minus_a":diff,"abs_diff":diff.abs(),"low":low,"high":high,"direction":format!("{low}->{high}")}));
        }
        if rs.len() >= 4 {
            records.extend(rs);
        }
    }
    let mut out = summaries(&records, times);
    out["interval_details"] = json!(records);
    out["matched_nodes"] = json!(rows.len());
    out["missing_nodes"] = json!(missing);
    out["flagged_edges"] = json!(records
        .iter()
        .map(|r| s(&r["edge"]))
        .collect::<BTreeSet<_>>()
        .len());
    Ok(out)
}

fn classify(p: &Value) -> Result<Value> {
    let city_region = p["city_region"].as_object().ok_or("missing city regions")?;
    let mut accepted: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    let mut pending: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    let mut main_names: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut warnings = vec![];
    let mut source_count = 0;
    let mut seen = BTreeSet::new();
    fn add(index: &mut BTreeMap<String, Vec<Value>>, alias: &Value, evidence: &Value) {
        let key = node_key(alias);
        if !key.is_empty() {
            let rows = index.entry(key).or_default();
            if !rows.contains(evidence) {
                rows.push(evidence.clone());
            }
        }
    }
    for row in p["main"].as_array().ok_or("missing main rows")? {
        let fields = &row[1];
        let city_raw = s(&fields["地市"]);
        let city = city_raw.strip_suffix('市').unwrap_or(&city_raw).to_string();
        if !city_region.contains_key(&city) {
            return Err(format!("来源存在不在指定21市中的地市：{city_raw}").into());
        }
        seen.insert(city.clone());
        let names: Vec<_> = regex("[、，,\n]")
            .split(fields["节点名称（顿号分隔）"].as_str().unwrap_or(""))
            .map(str::trim)
            .filter(|n| !n.is_empty())
            .map(str::to_string)
            .collect();
        source_count += names.len();
        let unique: BTreeSet<_> = names.iter().collect();
        if fields["去重节点数量"].as_f64() != Some(unique.len() as f64) {
            warnings.push(format!(
                "各市节点名单!B{}：{}数量栏为 {}，实际名称 {} 个；按实际名单匹配，未修改原表。",
                row[0],
                city_raw,
                match &fields["去重节点数量"] {
                    Value::Null => "None".to_string(),
                    Value::Bool(value) => if *value { "True" } else { "False" }.to_string(),
                    value => text(value),
                },
                unique.len()
            ));
        }
        for name in names {
            main_names
                .entry(name.clone())
                .or_default()
                .insert(city.clone());
            add(
                &mut accepted,
                &json!(name),
                &json!({"city":city,"source_city":city_raw,"source_node":name,"sheet":"各市节点名单","row":row[0],"source_status":"地市主表"}),
            );
        }
    }
    if seen.len() != city_region.len() {
        let missing: Vec<_> = city_region.keys().filter(|c| !seen.contains(*c)).collect();
        return Err(format!("来源主表缺少地市：{missing:?}").into());
    }
    for row in p["supplement"].as_array().ok_or("missing supplement")? {
        let f = &row[1];
        let source = s(&f["补充记录统一节点名"]);
        let raw = s(&f["匹配地市"]);
        let city = raw.strip_suffix('市').unwrap_or(&raw).to_string();
        let status = text(&f["识别状态"]);
        let ev = json!({"city":if city_region.contains_key(&city){json!(city)}else{Value::Null},"source_city":raw,"source_node":source,"sheet":"补充节点待核验","row":row[0],"source_status":status});
        let verified = status == "已匹配地市主表"
            && main_names.get(&source).is_some_and(|c| c.contains(&city));
        let index = if verified {
            &mut accepted
        } else {
            &mut pending
        };
        add(index, &json!(source), &ev);
        for alias in text(&f["原始节点名称（逐条）"]).lines() {
            add(index, &json!(alias), &ev);
        }
    }
    let connected: BTreeSet<_> = p["connected"]
        .as_array()
        .ok_or("missing connected")?
        .iter()
        .map(s)
        .collect();
    let nodes: BTreeSet<_> = p["nodes"]
        .as_array()
        .ok_or("missing nodes")?
        .iter()
        .map(s)
        .collect();
    let mut records = vec![];
    for name in &nodes {
        let key = node_key(&json!(name));
        let mut evidence = accepted.get(&key).cloned().unwrap_or_default();
        let cities: BTreeSet<_> = evidence.iter().map(|e| s(&e["city"])).collect();
        let mut city = if cities.len() == 1 {
            Some(cities.first().unwrap().clone())
        } else {
            None
        };
        if let Some(c) = p["confirmed_cities"].get(name) {
            let c = s(c);
            if !city_region.contains_key(&c) {
                return Err(format!("确认地市不在指定21市中：{c}").into());
            }
            city = Some(c.clone());
            evidence = vec![
                json!({"city":c,"source_city":format!("{c}市"),"source_node":name,"sheet":"用户确认","row":Value::Null,"source_status":"2026-10-23用户确认"}),
            ];
        }
        let candidate = pending.get(&key).cloned().unwrap_or_default();
        let reason = if cities.len() > 1 {
            "来源存在多个地市归属，需确认"
        } else if !candidate.is_empty() {
            "来源补充表标记待核验，暂不归类"
        } else {
            "来源表未找到可确认的同名节点；不按相邻接线或相似名称推断"
        };
        let region = city
            .as_ref()
            .and_then(|c| city_region.get(c))
            .cloned()
            .unwrap_or(Value::Null);
        let classified = city.is_some();
        if !classified {
            evidence.extend(candidate);
        }
        records.push(json!({"name":name,"region":region,"city":city,"status":if classified{"classified"}else{"unclassified"},"connected":connected.contains(name),"evidence":evidence,"reason":if classified{""}else{reason}}));
    }
    let mut hierarchy = vec![];
    for (i, region) in p["regions"]
        .as_array()
        .ok_or("missing regions")?
        .iter()
        .enumerate()
    {
        let name = &region[0];
        let cities = region[1].as_array().ok_or("missing cities")?;
        let cs:Vec<_>=cities.iter().map(|c|json!({"name":c,"nodes":records.iter().filter(|r|r["city"]==*c).map(|r|r["name"].clone()).collect::<Vec<_>>()})).collect();
        hierarchy.push(json!({"name":name,"label":format!("{}. {}（{}市）",i+1,s(name),cities.len()),"cities":cs}));
    }
    let unclassified: Vec<_> = records
        .iter()
        .filter(|r| r["status"] == "unclassified")
        .cloned()
        .collect();
    let isolated: Vec<_> = nodes.difference(&connected).cloned().collect();
    if !isolated.is_empty() {
        warnings.push(format!(
            "拓扑第一部分列出、但第二部分尚无接线边的节点：{}",
            isolated.join("、")
        ));
    }
    Ok(
        json!({"schema_version":1,"summary":{"topology_nodes":nodes.len(),"connected_nodes":connected.len(),"classified_nodes":records.len()-unclassified.len(),"unclassified_nodes":unclassified.len(),"source_listed_names":source_count},"regions":hierarchy,"nodes":records,"unclassified":unclassified,"warnings":warnings}),
    )
}
pub fn execute(p: &Value) -> Result<Value> {
    match p["action"].as_str().unwrap_or("") {
        "normalize" => {
            let names = p["names"].as_array().ok_or("missing names")?;
            Ok(
                json!({"price":names.iter().map(price_name).collect::<Vec<_>>(),"node":names.iter().map(node_name).collect::<Vec<_>>(),"key":names.iter().map(node_key).collect::<Vec<_>>()}),
            )
        }
        "topology" => topology(p["text"].as_str().ok_or("missing topology text")?),
        "classify" => classify(p),
        "dedupe" => dedupe(p),
        "analyze" => analyze(p),
        "match" => Ok(json!(matched(
            p["nodes"].as_array().ok_or("missing nodes")?,
            p["rows"].as_array().ok_or("missing rows")?
        ))),
        "mean" => mean(p["rows"].as_array().ok_or("missing rows")?),
        "summaries" => Ok(summaries(
            p["records"].as_array().ok_or("missing records")?,
            p["time_headers"].as_array().ok_or("missing time headers")?,
        )),
        _ => Err("unknown sections action".into()),
    }
}
