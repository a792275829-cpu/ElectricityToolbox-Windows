//! Forecast kernels. DataFrame coercion/calendar source lookup stay in the adapter.
use chrono::{Datelike, Duration, NaiveDate};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    error::Error,
};
type E = Box<dyn Error>;
fn strings(p: &Value, k: &str) -> Result<Vec<String>, E> {
    Ok(p[k]
        .as_array()
        .ok_or("missing array")?
        .iter()
        .map(|v| v.as_str().unwrap_or("").to_owned())
        .collect())
}
fn nums(v: &Value) -> Vec<f64> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|v| {
            v.as_f64().unwrap_or_else(|| match v.as_str() {
                Some("Infinity") => f64::INFINITY,
                Some("-Infinity") => f64::NEG_INFINITY,
                _ => f64::NAN,
            })
        })
        .collect()
}
fn metric_json(value: f64) -> Value {
    if value == f64::INFINITY {
        json!("Infinity")
    } else if value == f64::NEG_INFINITY {
        json!("-Infinity")
    } else {
        json!(value)
    }
}
fn mean(v: &[f64]) -> f64 {
    let a: Vec<_> = v.iter().filter(|x| !x.is_nan()).collect();
    if a.is_empty() {
        f64::NAN
    } else {
        a.iter().map(|x| **x).sum::<f64>() / a.len() as f64
    }
}
fn std(v: &[f64]) -> f64 {
    let a: Vec<_> = v.iter().copied().filter(|x| !x.is_nan()).collect();
    if a.len() < 2 {
        return f64::NAN;
    }
    let m = mean(&a);
    (a.iter().map(|x| (x - m).powi(2)).sum::<f64>() / (a.len() - 1) as f64).sqrt()
}
fn median(v: &[f64]) -> f64 {
    let mut a: Vec<_> = v.iter().copied().filter(|x| !x.is_nan()).collect();
    a.sort_by(f64::total_cmp);
    if a.len() < 2 {
        return f64::NAN;
    }
    (a[(a.len() - 1) / 2] + a[a.len() / 2]) / 2.
}
fn add(out: &mut Vec<(String, Vec<f64>)>, name: &str, v: Vec<f64>) {
    if let Some((_, old)) = out.iter_mut().find(|(key, _)| key == name) {
        *old = v;
    } else {
        out.push((name.to_owned(), v));
    }
}
fn feature_values(v: &Value, binary: Option<&[u8]>, rows: usize) -> Result<Vec<f64>, E> {
    let values = if let Some(bytes) = binary {
        let offset = usize::try_from(v["offset"].as_u64().ok_or("missing binary offset")?)?;
        let length = usize::try_from(v["length"].as_u64().ok_or("missing binary length")?)?;
        if length != rows || offset % 8 != 0 {
            return Err("invalid binary column shape/alignment".into());
        }
        let end = offset
            .checked_add(length.checked_mul(8).ok_or("binary size overflow")?)
            .ok_or("binary offset overflow")?;
        let slice = bytes
            .get(offset..end)
            .ok_or("binary column out of bounds")?;
        slice
            .chunks_exact(8)
            .map(|chunk| f64::from_le_bytes(chunk.try_into().unwrap()))
            .collect()
    } else {
        if !v.is_array() {
            return Err("missing numeric array".into());
        }
        nums(v)
    };
    if values.len() != rows {
        return Err("numeric column row count mismatch".into());
    }
    Ok(values)
}
pub fn execute(p: &Value) -> Result<Value, E> {
    if p["action"] == "baseline" {
        let values = p["values"].as_array().ok_or("missing baseline values")?;
        let shift = p["shift"].as_u64().ok_or("missing baseline shift")? as usize;
        return Ok(json!((0..values.len())
            .map(|i| if i >= shift {
                values[i - shift].clone()
            } else {
                Value::Null
            })
            .collect::<Vec<_>>()));
    }
    let ds = strings(p, "dates")?;
    let dates: Vec<NaiveDate> = ds
        .iter()
        .map(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d"))
        .collect::<Result<_, _>>()?;
    let n = dates.len();
    let action = p["action"].as_str().unwrap_or("");
    let binary = if !p["binary"].is_null() {
        if action != "features"
            || p["binary"]["dtype"] != "<f8"
            || p["binary"]["rows"].as_u64() != Some(n as u64)
        {
            return Err("invalid binary feature format/shape".into());
        }
        let bytes = std::fs::read(
            p["binary"]["input_path"]
                .as_str()
                .ok_or("missing binary input path")?,
        )?;
        if bytes.len() % 8 != 0 || p["binary"]["bytes"].as_u64() != Some(bytes.len() as u64) {
            return Err("binary file length mismatch".into());
        }
        Some(bytes)
    } else {
        None
    };

    if action == "splits" {
        let mut unique = dates.clone();
        unique.sort();
        unique.dedup();
        let h = p["holdout_days"].as_u64().unwrap_or(14) as usize;
        let v = p["validation_days"].as_u64().unwrap_or(7) as usize;
        let k = p["n_splits"].as_u64().unwrap_or(3) as usize;
        let req = h + v * k + 1;
        if unique.len() < req {
            return Err(format!("至少需要{req}个完整日期，当前只有{}个", unique.len()).into());
        }
        let dev = if h == 0 { 0 } else { unique.len() - h };
        let indices = |set: &[NaiveDate]| -> Vec<usize> {
            dates
                .iter()
                .enumerate()
                .filter_map(|(i, d)| set.contains(d).then_some(i))
                .collect()
        };
        let folds: Vec<_> = (1..=k)
            .rev()
            .map(|s| {
                if h == 0 {
                    return json!([Vec::<usize>::new(), Vec::<usize>::new()]);
                }
                let end = dev - v * (s - 1);
                json!([indices(&unique[..end - v]), indices(&unique[end - v..end])])
            })
            .collect();
        return Ok(json!({"folds":folds,"holdout":indices(&unique[dev..])}));
    }
    let slots = strings(p, "slots")?;
    if slots.len() != n {
        return Err("date/slot length mismatch".into());
    }
    if action == "evaluate" {
        let a = nums(&p["true"]);
        let b = nums(&p["pred"]);
        if a.len() != n || b.len() != n {
            return Err("prediction length mismatch".into());
        }
        let e: Vec<_> = a.iter().zip(b).map(|(a, b)| (a - b).abs()).collect();
        let mut daily: BTreeMap<String, Vec<f64>> = BTreeMap::new();
        let mut slot: BTreeMap<String, Vec<f64>> = BTreeMap::new();
        for i in 0..n {
            daily.entry(ds[i].clone()).or_default().push(e[i]);
            slot.entry(slots[i].clone()).or_default().push(e[i]);
        }
        return Ok(
            json!({"mae":metric_json(mean(&e)),"daily":daily.iter().map(|(d,v)|json!([d,metric_json(mean(v))])).collect::<Vec<_>>(),"slots":slot.iter().map(|(d,v)|json!([d,metric_json(mean(v))])).collect::<Vec<_>>()}),
        );
    }
    if action == "normalize" {
        let slots = slots
            .iter()
            .map(|s| {
                let mut parts = s.split(':');
                let h: u32 = parts.next().ok_or("invalid hour")?.parse()?;
                let m: u32 = parts.next().ok_or("invalid minute")?.parse()?;
                if h > 23 || m > 59 {
                    return Err("时刻字段包含无法解析的值".into());
                }
                Ok(format!("{h:02}:{m:02}"))
            })
            .collect::<Result<Vec<String>, E>>()?;
        let mut order: Vec<usize> = (0..n).collect();
        order.sort_by(|&a, &b| (dates[a], &slots[a]).cmp(&(dates[b], &slots[b])));
        return Ok(
            json!({"order":order,"dates":dates.iter().map(|d|d.to_string()).collect::<Vec<_>>(),"slots":slots}),
        );
    }
    let y = feature_values(&p["target"], binary.as_deref(), n)?;
    if y.len() != n {
        return Err("target length mismatch".into());
    }
    let mut lookup = HashMap::new();
    let mut groups: BTreeMap<NaiveDate, Vec<usize>> = BTreeMap::new();
    for i in 0..n {
        if lookup.insert((dates[i], slots[i].clone()), i).is_some() && action != "labeled" {
            return Err("日期+时刻存在重复记录".into());
        }
        groups.entry(dates[i]).or_default().push(i);
    }
    if action == "grain" {
        for (d, ix) in &groups {
            if ix.len() != 96 {
                return Err(format!("每天必须有96个时点，异常日期: {d}: {}", ix.len()).into());
            }
        }
        let expected: HashSet<_> = (0..96)
            .map(|x| format!("{:02}:{:02}", x / 4, x % 4 * 15))
            .collect();
        if slots.iter().any(|s| !expected.contains(s)) {
            return Err("时刻必须为00:00至23:45的标准15分钟时点".into());
        }
        if let (Some(a), Some(b)) = (groups.keys().next(), groups.keys().last()) {
            if (*b - *a).num_days() + 1 != groups.len() as i64 {
                return Err("日期不连续，缺少日期".into());
            }
        }
        return Ok(json!(true));
    }
    if action == "labeled" {
        let mut labeled: Vec<usize> = Vec::new();
        let mut future: Vec<usize> = Vec::new();
        for (d, ix) in &groups {
            let count = ix.iter().filter(|&&i| !p["target"][i].is_null()).count();
            if count != 0 && count != ix.len() {
                return Err(format!("目标存在部分缺失日期: {d}").into());
            }
            if count == 0 {
                future.extend(ix.iter().copied())
            } else {
                labeled.extend(ix.iter().copied())
            }
        }
        labeled.sort();
        future.sort();
        return Ok(json!({"labeled":labeled,"future":future}));
    }
    if action != "features" {
        return Err(format!("unknown forecast action: {action}").into());
    }
    // Calendar-key lookup is essential: a missing date must never change D-2 to D-1.
    let lag_positions: HashMap<i64, Vec<Option<usize>>> = [1, 2, 3, 7, 14]
        .into_iter()
        .map(|days| {
            (
                days,
                (0..n)
                    .map(|i| {
                        lookup
                            .get(&(dates[i] - Duration::days(days), slots[i].clone()))
                            .copied()
                    })
                    .collect(),
            )
        })
        .collect();
    let lag = |v: &Vec<f64>, days: i64| -> Vec<f64> {
        lag_positions[&days]
            .iter()
            .map(|j| j.map(|i| v[i]).unwrap_or(f64::NAN))
            .collect()
    };
    let mut out = Vec::new();
    let slot: Vec<f64> = slots
        .iter()
        .map(|s| {
            Ok::<_, E>(s[..2].parse::<u32>()? as f64 * 4. + (s[3..5].parse::<u32>()? / 15) as f64)
        })
        .collect::<Result<_, _>>()?;
    add(&mut out, "slot", slot.clone());
    add(
        &mut out,
        "weekday",
        dates
            .iter()
            .map(|d| d.weekday().num_days_from_monday() as f64)
            .collect(),
    );
    add(
        &mut out,
        "month",
        dates.iter().map(|d| d.month() as f64).collect(),
    );
    add(
        &mut out,
        "day_of_year",
        dates.iter().map(|d| d.ordinal() as f64).collect(),
    );
    add(
        &mut out,
        "is_weekend",
        dates
            .iter()
            .map(|d| {
                if d.weekday().num_days_from_monday() >= 5 {
                    1.
                } else {
                    0.
                }
            })
            .collect(),
    );
    add(
        &mut out,
        "slot_sin",
        slot.iter()
            .map(|x| (2. * std::f64::consts::PI * x / 96.).sin())
            .collect(),
    );
    add(
        &mut out,
        "slot_cos",
        slot.iter()
            .map(|x| (2. * std::f64::consts::PI * x / 96.).cos())
            .collect(),
    );
    for d in [1, 2, 3, 7, 14] {
        add(&mut out, &format!("price_lag_{d}d"), lag(&y, d));
    }
    let l1 = lag(&y, 1);
    let l7 = lag(&y, 7);
    add(
        &mut out,
        "price_lag_1d_minus_7d",
        l1.iter().zip(l7).map(|(a, b)| a - b).collect(),
    );
    let diff = |v: &Vec<f64>| -> Vec<f64> {
        (0..n)
            .map(|i| {
                if i > 0 && dates[i] == dates[i - 1] {
                    v[i] - v[i - 1]
                } else {
                    f64::NAN
                }
            })
            .collect()
    };
    add(&mut out, "previous_day_price_ramp", diff(&l1));
    for metric in ["mean", "std", "range"] {
        let stats: HashMap<_, _> = groups
            .iter()
            .map(|(d, ix)| {
                let v: Vec<_> = ix.iter().map(|&i| y[i]).collect();
                let value = match metric {
                    "mean" => mean(&v),
                    "std" => std(&v),
                    _ => {
                        v.iter()
                            .copied()
                            .filter(|x| !x.is_nan())
                            .fold(f64::NEG_INFINITY, f64::max)
                            - v.iter()
                                .copied()
                                .filter(|x| !x.is_nan())
                                .fold(f64::INFINITY, f64::min)
                    }
                };
                (*d, value)
            })
            .collect();
        add(
            &mut out,
            &format!("previous_day_price_{metric}"),
            dates
                .iter()
                .map(|d| *stats.get(&(*d - Duration::days(1))).unwrap_or(&f64::NAN))
                .collect(),
        );
    }
    // Match pandas shift(1).rolling(window): windows count prior observations,
    // unlike the explicit calendar-key joins above. Sample std uses ddof=1.
    let mut history: HashMap<&str, Vec<f64>> = HashMap::new();
    let mut rolling: Vec<Vec<f64>> = vec![vec![f64::NAN; n]; 9];
    for i in 0..n {
        let h = history.entry(&slots[i]).or_default();
        for (wi, w) in [3, 7, 14].iter().enumerate() {
            let a: Vec<f64> = h[h.len().saturating_sub(*w)..]
                .iter()
                .map(|v| if v.is_finite() { *v } else { f64::NAN })
                .collect();
            let valid = a.iter().filter(|v| !v.is_nan()).count();
            if valid >= 2 {
                rolling[wi * 3][i] = mean(&a);
                rolling[wi * 3 + 1][i] = median(&a);
                rolling[wi * 3 + 2][i] = std(&a);
            }
        }
        h.push(y[i]);
    }
    for (wi, w) in [3, 7, 14].iter().enumerate() {
        for (mi, m) in ["mean", "median", "std"].iter().enumerate() {
            add(
                &mut out,
                &format!("price_same_slot_{m}_{w}d"),
                rolling[wi * 3 + mi].clone(),
            );
        }
    }
    if let Some(cal) = p["calendar"].as_array() {
        for c in cal {
            add(
                &mut out,
                c["name"].as_str().ok_or("missing calendar name")?,
                feature_values(&c["values"], binary.as_deref(), n)?,
            );
        }
    }
    let mode = p["exogenous_mode"].as_str().unwrap_or("legacy");
    if !["legacy", "none", "lagged", "published"].contains(&mode) {
        return Err(format!("未知外生数据方式: {mode}").into());
    }
    let mut raw: Vec<(String, Vec<f64>)> = Vec::new();
    if mode != "none" {
        if let Some(cols) = p["raw"].as_array() {
            for c in cols {
                let v = feature_values(&c["values"], binary.as_deref(), n)?;
                if mode != "legacy" || v.iter().filter(|x| !x.is_nan()).count() >= 672 {
                    raw.push((c["name"].as_str().unwrap().to_owned(), v));
                }
            }
        }
    }
    if p["supply"].as_bool() == Some(true) {
        let get = |s: &str| raw.iter().find(|(k, _)| k == s).map(|(_, v)| v.clone());
        if let (Some(load), Some(wind), Some(solar)) = (
            get("日前统调负荷(MW)"),
            get("日前风电出力(MW)"),
            get("日前光伏出力(MW)"),
        ) {
            let renew: Vec<_> = wind.iter().zip(solar).map(|(a, b)| a + b).collect();
            let net: Vec<_> = load.iter().zip(&renew).map(|(a, b)| a - b).collect();
            raw.push(("风光出力合计参考MW".into(), renew.clone()));
            raw.push((
                "风光占统调负荷参考比".into(),
                renew
                    .iter()
                    .zip(&load)
                    .map(|(a, b)| if *b > 0. { a / b } else { f64::NAN })
                    .collect(),
            ));
            raw.push(("负荷扣风光参考MW".into(), net.clone()));
            for (name, v) in [
                ("日前统调负荷(MW)", load),
                ("风光出力合计参考MW", renew),
                ("负荷扣风光参考MW", net),
            ] {
                raw.push((format!("{name}_时点变化"), diff(&v)));
            }
        }
        let get = |s: &str| raw.iter().find(|(k, _)| k == s).map(|(_, v)| v.clone());
        if let (Some(load), Some(space)) = (get("日前统调负荷(MW)"), get("日前B类竞价空间(MW)"))
        {
            raw.push((
                "竞价空间占统调负荷参考比".into(),
                space
                    .iter()
                    .zip(load)
                    .map(|(a, b)| if b > 0. { a / b } else { f64::NAN })
                    .collect(),
            ));
            raw.push(("竞价空间_时点变化".into(), diff(&space)));
        }
    }
    for (name, v) in raw {
        let days = if mode == "lagged" {
            vec![1, 7]
        } else {
            vec![0]
        };
        for d in days {
            let key = if d == 0 {
                name.clone()
            } else {
                format!("{name}__lag_{d}d")
            };
            let values = if d == 0 { v.clone() } else { lag(&v, d) };
            add(&mut out, &key, values.clone());
            add(
                &mut out,
                &format!("{key}__missing"),
                values
                    .iter()
                    .map(|x| if x.is_nan() { 1. } else { 0. })
                    .collect(),
            );
        }
    }
    if let Some(cols) = p["weather"].as_array() {
        for c in cols {
            let name = c["name"].as_str().unwrap();
            let v = lag(&feature_values(&c["values"], binary.as_deref(), n)?, 1);
            add(&mut out, &format!("{name}__weather_lag_1d"), v.clone());
            add(
                &mut out,
                &format!("{name}__weather_missing"),
                v.iter().map(|x| if x.is_nan() { 1. } else { 0. }).collect(),
            );
        }
    }
    if binary.is_some() {
        use std::io::{BufWriter, Write};
        let path = p["binary"]["output_path"]
            .as_str()
            .ok_or("missing binary output path")?;
        let file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)?;
        let mut writer = BufWriter::new(file);
        for (_, values) in &out {
            if values.len() != n {
                return Err("binary output column shape mismatch".into());
            }
            for value in values {
                let finite = if value.is_infinite() {
                    f64::NAN
                } else {
                    *value
                };
                writer.write_all(&finite.to_le_bytes())?;
            }
        }
        writer.flush()?;
        Ok(
            json!({"columns":out.iter().map(|(name,_)|name).collect::<Vec<_>>(), "rows":n,"dtype":"<f8","order":"column-major","bytes":out.len()*n*8}),
        )
    } else {
        Ok(json!(out
            .into_iter()
            .map(|(name, values)| json!({"name":name,"values":values}))
            .collect::<Vec<_>>()))
    }
}
