use chrono::{Duration, NaiveDate};
use serde_json::{json, Value};
use std::error::Error;
type R = Result<Value, Box<dyn Error>>;
fn arr(v: &Value) -> &[Value] {
    v.as_array().map(Vec::as_slice).unwrap_or(&[])
}
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|n| n != 0.).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}
fn txt(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        _ => v.to_string(),
    }
}
fn dt(v: &Value) -> String {
    txt(v).chars().take(10).collect()
}
fn market_date(v: &Value) -> String {
    let text = txt(v);
    for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y年%m月%d日"] {
        if let Ok(d) = NaiveDate::parse_from_str(text.trim(), fmt) {
            return d.to_string();
        }
    }
    String::new()
}
fn trade_date(v: &Value) -> String {
    if let Some(n) = v.as_f64() {
        if (0.0..1.0).contains(&n) {
            return String::new();
        }
        let offset = if (1.0..60.0).contains(&n) { 1 } else { 0 };
        return (NaiveDate::from_ymd_opt(1899, 12, 30).unwrap()
            + Duration::days(n.floor() as i64 + offset))
        .to_string();
    }
    let text: String = txt(v).trim().chars().take(10).collect();
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y%m%d"] {
        if let Ok(d) = NaiveDate::parse_from_str(text.trim(), fmt) {
            return d.to_string();
        }
    }
    String::new()
}

fn num(v: &Value) -> Option<f64> {
    let n = match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    };
    n.filter(|n| n.is_finite())
}
fn rounded(n: f64, d: usize) -> f64 {
    format!("{:.*}", d, n).parse().unwrap_or(n)
}
fn required(v: &Value, label: &str, d: usize) -> Result<f64, Box<dyn Error>> {
    num(v)
        .map(|n| rounded(n, d))
        .ok_or_else(|| -> Box<dyn Error> { format!("{label}不是有效有限数值").into() })
}
fn period<'a>(rows: &'a Value, date: &str) -> Option<&'a Value> {
    arr(rows)
        .iter()
        .rev()
        .filter(|r| dt(&r["date"]) == date)
        .max_by(|a, b| txt(&a["versionDate"]).cmp(&txt(&b["versionDate"])))
}
fn metric(p: &Value, field: &str) -> Option<f64> {
    if let Some(n) = num(&p[field]) {
        return Some(n);
    }
    let ns: Vec<f64> = arr(&p["loadVal"]).iter().filter_map(num).collect();
    if ns.is_empty() {
        return None;
    }
    match field {
        "max" => ns.iter().copied().reduce(f64::max),
        "min" => ns.iter().copied().reduce(f64::min),
        "avg" => Some(ns.iter().sum::<f64>() / ns.len() as f64),
        "sum" => Some(ns.iter().sum::<f64>() * 0.25 / 100000.),
        _ => None,
    }
}
fn dated(p: &Value) -> Result<(String, String), Box<dyn Error>> {
    let s = txt(&p["date"]);
    let d = NaiveDate::parse_from_str(&s, "%Y-%m-%d")?;
    Ok((s, (d - Duration::days(1)).to_string()))
}
fn load_items(p: &Value) -> &[Value] {
    arr(&p["data"]["dataNetLoadDTOList"])
}
fn series<'a>(items: &'a [Value], kind: &str) -> &'a Value {
    items
        .iter()
        .find(|i| txt(&i["loadType"]) == kind)
        .unwrap_or(&Value::Null)
}
fn board<'a>(p: &'a Value, date: &str, kind: &str) -> &'a Value {
    arr(&p["data"]["dataApplyConfigDOList"])
        .iter()
        .find(|r| {
            dt(&r["date"]) == date
                && txt(&r["dataType"]) == kind
                && (r["id"] != "" || truthy(&r["createTime"]) || truthy(&r["updateTime"]))
        })
        .unwrap_or(&Value::Null)
}
fn card(label: &str, value: Option<f64>, base: Option<f64>, unit: &str) -> Value {
    json!({"label":label,"value":value,"unit":unit,"delta":value.zip(base).map(|(v,b)|v-b),"pending":value.is_none(),"comparison_value":base})
}
fn maintenance(current: &Value, base: &Value, date: &str) -> Value {
    let v = num(&current["overHaulCapacity"]).map(|v| v / 10.);
    let b = num(&base["overHaulCapacity"]).map(|v| v / 10.);
    let mut c = card("检修容量", v, b, "万千瓦");
    c["comparison_date"] = json!(date);
    c["source"] = json!("清能系统市场行情看板");
    if v.is_some() && b.is_none() {
        c["comparison_note"] = json!("前一日日前对比待更新")
    };
    if v.is_none() {
        c["pending_reason"] = json!("清能系统未提供有效日前检修记录")
    };
    c
}
fn thermal_series(p: &Value, groups: &Value, kind: &str) -> Option<Vec<Vec<f64>>> {
    let mut out = vec![];
    for g in arr(groups) {
        let row = arr(&p["data"])
            .iter()
            .find(|r| txt(&r["dataType"]) == kind && r["dataItemGroup"] == *g)?;
        let ns: Option<Vec<_>> = arr(&row["unitCount"]).iter().map(num).collect();
        let ns = ns?;
        if ns.len() != 24 || ns.iter().any(|n| *n < 0. || n.fract() != 0.) {
            return None;
        }
        out.push(ns)
    }
    Some(out)
}
fn breakdown(p: &Value, groups: &Value, kind: &str, partial: bool) -> Value {
    let mut out = json!({"basis":"coal_mode_gas_1900","coal_basis":"mode","gas_basis":"19:00","tie_breaker":"earliest_occurrence","coal":null,"gas":null});
    let full = thermal_series(p, groups, kind);
    if full.is_none() && !partial {
        return out;
    }
    for (i, fuel) in ["coal", "gas"].iter().enumerate() {
        let one = if let Some(s) = &full {
            s.get(i).cloned()
        } else {
            thermal_series(p, &json!([groups[i]]), kind).and_then(|s| s.first().cloned())
        };
        if let Some(ns) = one {
            if i == 1 {
                out[*fuel] = json!(ns[19]);
                out["gas_hour_index"] = json!(19);
            } else {
                let mut counts: Vec<(f64, usize)> = vec![];
                for n in &ns {
                    if let Some(t) = counts.iter_mut().find(|t| t.0 == *n) {
                        t.1 += 1
                    } else {
                        counts.push((*n, 1))
                    }
                }
                let freq = counts.iter().map(|x| x.1).max().unwrap_or(0);
                let cs: Vec<f64> = counts.iter().filter(|x| x.1 == freq).map(|x| x.0).collect();
                out["coal"] = json!(cs[0]);
                out["coal_mode_frequency"] = json!(freq);
                out["coal_mode_candidates"] = json!(cs);
                out["coal_mode_hours"] = json!(ns
                    .iter()
                    .enumerate()
                    .filter(|(_, n)| **n == cs[0])
                    .map(|(i, _)| i)
                    .collect::<Vec<_>>());
            }
        }
    }
    out
}
fn compare(current: &Value, base: &Value) -> Value {
    let mut out = current.clone();
    for fuel in ["coal", "gas"] {
        out[format!("{fuel}_comparison")] = base[fuel].clone();
        out[format!("{fuel}_delta")] = json!(num(&current[fuel])
            .zip(num(&base[fuel]))
            .map(|(a, b)| a - b));
        let fs = if fuel == "coal" {
            vec!["mode_frequency", "mode_candidates", "mode_hours"]
        } else {
            vec!["hour_index"]
        };
        for f in fs {
            out[format!("{fuel}_comparison_{f}")] = base[format!("{fuel}_{f}")].clone()
        }
    }
    out
}
fn thermal_card(cur: &Value, base: &Value, label: &str) -> Value {
    let v = num(&cur["coal"]).zip(num(&cur["gas"])).map(|(a, b)| a + b);
    let b = num(&base["coal"])
        .zip(num(&base["gas"]))
        .map(|(a, b)| a + b);
    let mut c = card(label, v, b, "台");
    c.as_object_mut().unwrap().remove("comparison_value");
    c["value_basis"] = json!("coal_mode_plus_gas_1900");
    c["source"] = json!("清能系统现货分时分类型出清电量");
    c["thermal_breakdown"] = compare(cur, base);
    if v.is_some() && b.is_none() {
        c["comparison_note"] = json!(if label == "在线机组数" {
            "同日日前对比待更新"
        } else {
            "前一日日前对比待更新"
        })
    }
    c
}
fn points(raw: &Value, scale: f64, label: &str) -> R {
    if raw.is_null() || raw == &json!([]) {
        return Ok(json!({"value":null}));
    }
    if !raw.is_array() || arr(raw).len() != 24 {
        return Err(format!("{label} 应有24个数").into());
    }
    if arr(raw).iter().any(|v| v.is_null() || v == "" || v == "-") {
        return Ok(json!({"value":null}));
    }
    let ns: Result<Vec<f64>, _> = arr(raw)
        .iter()
        .map(|v| {
            num(v).map(|n| n * scale).ok_or_else(|| -> Box<dyn Error> {
                format!("{label}不是有效有限数值").into()
            })
        })
        .collect();
    Ok(json!({"value":ns?}))
}
fn load_summary(p: &Value, transaction: bool) -> R {
    let data = &p["dataset"];
    let items = load_items(data);
    let kind = txt(&p["load_type"]);
    let s = series(items, &kind);
    if s.is_null() {
        return Err(format!("缺少 loadType={kind}").into());
    }
    let periods = &s[txt(&p["period_key"])];
    let rows = arr(periods);
    // An unpublished market-load summary is a business availability state,
    // not a malformed numeric value. Preserve its public RuntimeError contract.
    if !transaction
        && (rows.is_empty()
            || ["max", "min", "avg", "sum"]
                .iter()
                .any(|field| rows[0][*field].is_null() || rows[0][*field] == ""))
    {
        return Ok(json!({"unavailable": format!(
            "{} loadType={kind} 没有返回{}数据", txt(&p["date"]), txt(&p["label"])
        )}));
    }
    let summary = if transaction {
        rows.iter()
            .find(|r| dt(&r["date"]) == txt(&p["date"]))
            .or_else(|| if rows.len() == 1 { rows.first() } else { None })
    } else {
        rows.first()
    }
    .ok_or("没有返回负荷数据")?;
    let mut ns = vec![];
    for f in ["max", "min", "avg", "sum"] {
        ns.push(required(&summary[f], f, if transaction { 10 } else { 4 })?)
    }
    if transaction {
        ns[3] = rounded(ns[3] * 100000., 10)
    }
    Ok(json!(ns))
}

fn status(hours: &Value, realtime: Option<f64>, actual: Option<f64>) -> (String, String) {
    let missing = arr(hours).len() != 24
        || arr(hours).iter().any(|h| {
            ["day_ahead_mwh", "realtime_mwh", "actual_mwh"]
                .iter()
                .any(|f| h[*f].is_null())
        });
    let (rt, act) = match (realtime, actual) {
        (Some(r), Some(a)) if !missing => (r, a),
        _ => {
            return (
                "数据不足".into(),
                "小时级日前、实时或实际电量存在缺失，无法完成完整日研判。".into(),
            )
        }
    };
    if rt >= 0.05 && act <= 0.01 {
        return (
            "中标未启机".into(),
            "实时出清已有中标电量，但实际发电量接近零。".into(),
        );
    }
    let mut positive = false;
    let mut low = 0;
    for h in arr(hours) {
        let a = num(&h["actual_mwh"]).unwrap_or(0.);
        let r = num(&h["realtime_mwh"]).unwrap_or(0.);
        if positive && a <= 0.1 && r > 0.1 {
            low += 1;
            if low >= 2 {
                return (
                    "疑似故障调停".into(),
                    "疑似故障调停，需结合运行记录确认。".into(),
                );
            }
        } else {
            low = 0
        }
        if a > 0. {
            positive = true
        }
    }
    if rt <= 0.01 && act <= 0.01 {
        return (
            "未中标/停备".into(),
            "实时出清和实际发电量均接近零，机组未中标或处于停备状态。".into(),
        );
    }
    let diff = act - rt;
    let tol = 0.02f64.max(rt * 0.03);
    let close = (diff.abs() - tol).abs() <= 1e-12f64.max(1e-12 * tol.max(diff.abs()));
    let rule = "容差取0.02万千瓦时与实时出清电量3%的较大值";
    if diff > tol && !close {
        ("超发".into(), format!("实际高于实时且超容差；{rule}。"))
    } else if diff < -tol && !close {
        ("欠发".into(), format!("实际低于实时且超容差；{rule}。"))
    } else {
        (
            "跟踪出力".into(),
            format!("实际与实时偏差在容差内；{rule}。"),
        )
    }
}
fn total(hours: &Value, field: &str) -> Option<f64> {
    if arr(hours).len() != 24 {
        return None;
    }
    let ns: Option<Vec<f64>> = arr(hours).iter().map(|h| num(&h[field])).collect();
    ns.map(|ns| rounded(ns.iter().sum::<f64>() / 10., 10))
}
fn summarize_units(p: &Value) -> R {
    let mut out = vec![];
    for plant in arr(&p["parsed"]["plants"]) {
        for unit in arr(&plant["units"]) {
            let h = &unit["hours"];
            let da = total(h, "day_ahead_mwh");
            let rt = total(h, "realtime_mwh");
            let act = total(h, "actual_mwh");
            let diff = act.zip(rt).map(|(a, r)| rounded(a - r, 10));
            let rate = diff
                .zip(rt)
                .and_then(|(d, r)| if r != 0. { Some(d / r) } else { None });
            let tol = rt.map(|r| rounded(0.02f64.max(r * 0.03), 10));
            let (state, explanation) = status(h, rt, act);
            out.push(json!({"plant":plant["plant"],"unit":unit["unit"],"day_ahead":da,"realtime":rt,"actual":act,"difference":diff,"difference_rate":rate,"tolerance":tol,"status":state,"explanation":explanation}));
        }
    }
    Ok(json!(out))
}

pub fn execute(p: &Value) -> R {
    let action = p["action"]
        .as_str()
        .ok_or("market_payload action 必须是字符串")?;
    if !p.is_object() {
        return Err("market_payload 参数必须是对象".into());
    }
    if ["daily_total", "classify_unit_status", "summarize_units"].contains(&action) {
        let mut hour_lists = vec![];
        if action == "summarize_units" {
            for plant in arr(&p["parsed"]["plants"]) {
                for unit in arr(&plant["units"]) {
                    hour_lists.push(&unit["hours"]);
                }
            }
        } else {
            hour_lists.push(&p["hours"])
        }
        for hours in hour_lists {
            if arr(hours).len() != 24 {
                continue;
            }
            let fields = if action == "daily_total" {
                vec![txt(&p["field"])]
            } else {
                vec![
                    "day_ahead_mwh".into(),
                    "realtime_mwh".into(),
                    "actual_mwh".into(),
                ]
            };
            if action == "classify_unit_status"
                && (num(&p["realtime"]).is_none()
                    || num(&p["actual"]).is_none()
                    || arr(hours)
                        .iter()
                        .any(|h| fields.iter().any(|f| h[f].is_null())))
            {
                continue;
            }
            for field in fields {
                if arr(hours).iter().any(|h| h[&field].is_null()) {
                    continue;
                }
                for hour in arr(hours) {
                    if num(&hour[&field]).is_none() {
                        return Err(format!("小时级字段 {field} 不是有效有限数值").into());
                    }
                }
            }
        }
    }

    if ["thermal_series", "thermal_counts", "thermal_breakdown"].contains(&action)
        && (!p["groups"].is_array()
            || action == "thermal_breakdown" && arr(&p["groups"]).len() != 2)
    {
        return Err("groups 必须是数组，燃煤燃气分项需要两个组".into());
    }
    match action {
        "classify_unit_status" => {
            let (a, b) = status(&p["hours"], num(&p["realtime"]), num(&p["actual"]));
            Ok(json!([a, b]))
        }
        "summarize_units" => summarize_units(p),

        "missing_west_dates" => {
            let (date, _) = dated(p)?;
            let d = NaiveDate::parse_from_str(&date, "%Y-%m-%d")?;
            let west = series(load_items(&p["payload"]), "4");
            let dates: Vec<_> = arr(&west["forecastPeriodList"])
                .iter()
                .map(|r| dt(&r["date"]))
                .collect();
            Ok(json!((1..=30)
                .map(|n| (d - Duration::days(n)).to_string())
                .filter(|d| !dates.contains(d))
                .collect::<Vec<_>>()))
        }
        "merge_load_payload" => {
            let mut target = p["target"].clone();
            if target["data"].is_null() {
                target["data"] = json!({})
            }
            if target["data"]["dataNetLoadDTOList"].is_null() {
                target["data"]["dataNetLoadDTOList"] = json!([])
            }
            let items = target["data"]["dataNetLoadDTOList"]
                .as_array_mut()
                .ok_or("dataNetLoadDTOList 不是数组")?;
            for incoming in load_items(&p["incoming"]) {
                let kind = txt(&incoming["loadType"]);
                if let Some(item) = items.iter_mut().rev().find(|r| txt(&r["loadType"]) == kind) {
                    for k in ["forecastPeriodList", "actualPeriodList"] {
                        if item[k].is_null() {
                            item[k] = json!([])
                        }
                        let values = item[k].as_array_mut().ok_or("period list 不是数组")?;
                        values.extend(arr(&incoming[k]).iter().cloned());
                    }
                } else {
                    let mut item = incoming.clone();
                    for k in ["forecastPeriodList", "actualPeriodList"] {
                        item[k] = json!(arr(&incoming[k]))
                    }
                    items.push(item);
                }
            }
            Ok(target)
        }

        "power_maximum_card" => {
            let (date, previous) = dated(p)?;
            let real = p["realtime"].as_bool().unwrap_or(false);
            let kind = txt(&p["load_type"]);
            let s = series(arr(&p["items"]), &kind);
            let v = period(
                &s[if real {
                    "actualPeriodList"
                } else {
                    "forecastPeriodList"
                }],
                &date,
            )
            .and_then(|v| metric(v, "max"))
            .map(|n| n / 10.);
            let b = period(
                &s["forecastPeriodList"],
                if real { &date } else { &previous },
            )
            .and_then(|v| metric(v, "max"))
            .map(|n| n / 10.);
            let mut c = card(&txt(&p["label"]), v, b, "万千瓦");
            if !real {
                if b.is_some() {
                    c["comparison_date"] = json!(previous)
                } else {
                    c["comparison_note"] = json!("前一日对比待更新")
                }
            } else if kind == "4" {
                c["comparison_date"] = json!(date);
                if v.is_some() && b.is_none() {
                    c["comparison_note"] = json!("同日日前对比待更新")
                }
            }
            Ok(c)
        }
        "thermal_counts" => {
            let ns = thermal_series(&p["payload"], &p["groups"], &txt(&p["data_type"]));
            let out = ns.and_then(|ns| {
                if ns.is_empty() {
                    None
                } else {
                    Some(
                        (0..24)
                            .map(|i| ns.iter().map(|s| s[i]).sum::<f64>())
                            .collect::<Vec<_>>(),
                    )
                }
            });
            Ok(json!({"value":out}))
        }
        "daily_total" => Ok(json!({"value":total(&p["hours"],&txt(&p["field"]))})),
        "market_board_row" => {
            let (date, _) = dated(p)?;
            Ok(json!({"value":board(&p["payload"],&date,&txt(&p["data_type"]))}))
        }
        "maintenance_card" => {
            let real = p["realtime"].as_bool().unwrap_or(false);
            let mut c = maintenance(&p["current"], &p["baseline"], &txt(&p["date"]));
            if real {
                if !c["comparison_note"].is_null() {
                    c["comparison_note"] = json!("同日日前对比待更新")
                }
                if !c["pending_reason"].is_null() {
                    c["pending_reason"] = json!("清能系统未提供有效实时检修记录")
                }
            }
            Ok(c)
        }
        "api_payload" => {
            let data = &p["payload"];
            let label = txt(&p["label"]);
            if !data.is_object() {
                return Err(format!("{label}失败：接口返回格式不正确").into());
            }
            let code = if truthy(&data["retCode"]) {
                txt(&data["retCode"])
            } else {
                data.get("code")
                    .map(|v| {
                        if v.is_null() {
                            "None".to_string()
                        } else {
                            txt(v)
                        }
                    })
                    .unwrap_or_default()
            };
            if !["T200", "200", ""].contains(&code.to_uppercase().as_str()) {
                let message = ["retMsg", "message", "msg", "retCode"]
                    .iter()
                    .map(|f| &data[*f])
                    .find(|v| !v.is_null() && **v != "")
                    .map(txt)
                    .unwrap_or_default();
                return Err(format!("{label}失败：{message}").into());
            }
            Ok(data.clone())
        }
        "period_for_date" => Ok(json!({"value":period(&p["periods"],&txt(&p["date"]))})),
        "period_metric" => Ok(json!({"value":metric(&p["period"],&txt(&p["field"]))})),
        "thermal_series" => {
            Ok(json!({"value":thermal_series(&p["payload"],&p["groups"],&txt(&p["data_type"]))}))
        }
        "thermal_breakdown" => Ok(breakdown(
            &p["payload"],
            &p["groups"],
            &txt(&p["data_type"]),
            p["allow_partial"].as_bool().unwrap_or(false),
        )),
        "compare_thermal" => Ok(compare(&p["current"], &p["comparison"])),
        "thermal_summary_card" => Ok(thermal_card(
            &p["current"],
            &p["baseline"],
            &txt(&p["label"]),
        )),
        "day_ahead_thermal_card" | "realtime_thermal_card" => {
            let da = json!(["DAY_AHEAD_CLEARANCE_COAL", "DAY_AHEAD_CLEARANCE_GAS"]);
            let rt = json!(["REAL_TIME_CLEARANCE_COAL", "REAL_TIME_CLEARANCE_GAS"]);
            let real = action == "realtime_thermal_card";
            Ok(thermal_card(
                &breakdown(
                    &p["current"],
                    if real { &rt } else { &da },
                    if real { "2" } else { "1" },
                    false,
                ),
                &breakdown(&p["baseline"], &da, "1", false),
                if real {
                    "在线机组数"
                } else {
                    "市场开机"
                },
            ))
        }
        "day_ahead_cards" | "realtime_cards" => {
            let (date, previous) = dated(p)?;
            let real = action == "realtime_cards";
            let items = load_items(&p["load_payload"]);
            let load = series(items, "1");
            let cur = period(
                &load[if real {
                    "actualPeriodList"
                } else {
                    "forecastPeriodList"
                }],
                &date,
            )
            .unwrap_or(&Value::Null);
            let baseline = period(
                &load["forecastPeriodList"],
                if real { &date } else { &previous },
            )
            .unwrap_or(&Value::Null);
            if !p["allow_partial"].as_bool().unwrap_or(false)
                && (load.is_null() || cur.is_null() || baseline.is_null())
            {
                return Err("缺少日前或实际统调负荷数据".into());
            }
            let mut cards = vec![];
            for (label, f) in if real {
                [("统调实际负荷峰值", "max"), ("统调实际负荷谷值", "min")]
            } else {
                [("统调负荷峰值", "max"), ("统调负荷谷值", "min")]
            } {
                cards.push(card(
                    label,
                    metric(cur, f).map(|n| n / 10.),
                    metric(baseline, f).map(|n| n / 10.),
                    "万千瓦",
                ))
            }
            if !real {
                let b = board(&p["market_payload"], &date, "1");
                let prev = board(&p["market_payload"], &previous, "1");
                let mut c = card(
                    "市场开机",
                    num(&b["onlineUnit"]),
                    num(&prev["onlineUnit"]),
                    "台",
                );
                c.as_object_mut().unwrap().remove("comparison_value");
                cards.push(c);
                cards.push(maintenance(b, prev, &previous));
            }
            for (label, kind) in [
                ("省内A类电源", "2"),
                ("地方电源出力", "3"),
                ("西电东送电力", "4"),
                ("B类竞价空间", "6"),
            ] {
                let s = series(items, kind);
                let v = period(
                    &s[if real {
                        "actualPeriodList"
                    } else {
                        "forecastPeriodList"
                    }],
                    &date,
                )
                .and_then(|v| metric(v, "max"))
                .map(|n| n / 10.);
                let b = period(
                    &s["forecastPeriodList"],
                    if real { &date } else { &previous },
                )
                .and_then(|v| metric(v, "max"))
                .map(|n| n / 10.);
                let mut c = card(label, v, b, "万千瓦");
                if !real {
                    if b.is_some() {
                        c["comparison_date"] = json!(previous)
                    } else {
                        c["comparison_note"] = json!("前一日对比待更新")
                    }
                } else if kind == "4" {
                    c["comparison_date"] = json!(date);
                    if v.is_some() && b.is_none() {
                        c["comparison_note"] = json!("同日日前对比待更新")
                    }
                }
                cards.push(c);
            }
            Ok(json!(cards))
        }
        "market_board_status" => {
            let (date, _) = dated(p)?;
            let b = board(&p["payload"], &date, "1");
            let actual = board(&p["payload"], &date, "2");
            let v = num(&actual["onlineUnit"]);
            let mut m = maintenance(b, b, &date);
            m["value_basis"] = json!("same_day_ahead");
            Ok(
                json!([{"label":"在线机组数","value":v,"unit":"台","delta":null,"pending":v.is_none()},m]),
            )
        }
        "market_load_summary" => load_summary(p, false),
        "transaction_load_summary" => load_summary(p, true),
        "points24" => points(&p["raw"], num(&p["scale"]).unwrap_or(1.), &txt(&p["label"])),
        "forecast_payload" => {
            let rows = &p["payload"]["data"];
            if !rows.is_null() && !rows.is_array() {
                return Err("功率预测 的 data 不是数组。".into());
            }
            let selected: Vec<_> = arr(rows)
                .iter()
                .filter(|r| txt(&r["unitId"]) == txt(&p["unit_id"]))
                .collect();
            if selected.len() > 1 {
                return Err("功率预测 同一场站返回多条记录，不能确定数据。".into());
            }
            let mut out = json!({});
            if let Some(r) = selected.first() {
                for (col, f) in [
                    ("C", "forecastPower24"),
                    (
                        "J",
                        if p["fallback"].as_bool().unwrap_or(false) {
                            "rawShortTermPower24"
                        } else {
                            "stPredictPowerList24"
                        },
                    ),
                ] {
                    let value = points(&r[f], 1., col)?;
                    if !value["value"].is_null() {
                        out[col] = value["value"].clone()
                    }
                }
            }
            Ok(out)
        }
        "trade_payload" => {
            let data = &p["payload"]["data"];
            let rows = if data.is_null() {
                &Value::Null
            } else if data.is_object() {
                &data["unitDateList"]
            } else {
                data
            };
            if !rows.is_null() && !rows.is_array() {
                return Err("出清结果 unitDateList 不是数组。".into());
            }
            let mut out = json!({});
            let kind = txt(&p["data_type"]);
            for r in arr(rows) {
                if txt(&r["unitId"]) != txt(&p["unit_id"]) {
                    continue;
                }
                let date = trade_date(&r["date"]);
                if !arr(&p["expected_dates"]).contains(&json!(date)) {
                    continue;
                }
                // Only an absent key defaults to the requested type. An explicit
                // null must fail, matching Python row.get("dataType", data_type).
                if let Some(returned_type) = r.get("dataType") {
                    if txt(returned_type) != kind {
                        return Err(format!("{date} 出清结果日前/实时类型不匹配。").into());
                    }
                }
                if !out[&date].is_null() {
                    return Err(format!("{date} 同一场站出清记录重复。").into());
                }
                let mut values = json!({});
                for (col, f, scale) in if kind == "1" {
                    vec![("K", "electricity", 10.), ("L", "acPrice", 1.)]
                } else {
                    vec![("N", "acPrice", 1.)]
                } {
                    let v = points(&r[f], scale, col)?;
                    if !v["value"].is_null() {
                        values[col] = v["value"].clone()
                    }
                }
                out[date] = values;
            }
            Ok(out)
        }
        "report_values" => {
            let date = txt(&p["date"]);
            let row = arr(&p["payload"])
                .iter()
                .find(|r| market_date(&r["date"]) == date)
                .ok_or("没有返回日期的数据")?;
            let mut out = vec![];
            for field in arr(&p["fields"]) {
                let names = if field.is_array() {
                    arr(field).to_vec()
                } else {
                    vec![field.clone()]
                };
                let v = names
                    .iter()
                    .map(|f| &row["info"][txt(f)])
                    .find(|v| !v.is_null() && **v != "")
                    .ok_or_else(|| {
                        format!(
                            "缺少字段：{}",
                            names.iter().map(txt).collect::<Vec<_>>().join("/")
                        )
                    })?;
                let percent = names.iter().any(|n| arr(&p["percent_fields"]).contains(n));
                let n = if percent {
                    let text = txt(v);
                    num(&json!(text.trim().trim_end_matches('%').trim()))
                        .ok_or("百分比不是有效有限数值")?
                        / 100.
                } else {
                    num(v).ok_or("报表字段不是有效有限数值")?
                };
                out.push(rounded(n, 4));
            }
            if out.len() != p["expected_count"].as_u64().unwrap_or(out.len() as u64) as usize {
                return Err("报表字段数量不匹配".into());
            }
            Ok(json!(out))
        }
        "board_values" => {
            let date = txt(&p["date"]);
            let row = arr(&p["payload"]["dataApplyConfigDOList"])
                .iter()
                .find(|r| market_date(&r["date"]) == date && txt(&r["dataType"]) == "1")
                .ok_or("市场行情看板没有返回日前在线机组数据")?;
            Ok(json!([
                required(&row["onlineUnit"], "在线机组数", 4)?,
                required(&row["overHaulCapacity"], "省内机组预测检修容量", 4)?
            ]))
        }
        "clearing_counts" => {
            let date = txt(&p["date"]);
            let mut rows = std::collections::HashMap::new();
            for r in arr(&p["payload"]) {
                if market_date(&r["date"]) == date && txt(&r["dataType"]) == "1" {
                    rows.insert(txt(&r["dataItemGroup"]), r);
                }
            }
            let coal = rows
                .get("DAY_AHEAD_CLEARANCE_COAL")
                .ok_or("缺少日前燃煤数据")?;
            let gas = rows
                .get("DAY_AHEAD_CLEARANCE_GAS")
                .ok_or("缺少日前燃气数据")?;
            if arr(&coal["unitCount"]).len() < 24 {
                return Err("日前燃煤缺少 23:00 开机台数".into());
            }
            let coal = required(&coal["unitCount"][23], "23:00 开机台数", 4)?;
            let gs: Result<Vec<_>, _> = arr(&gas["unitCount"])
                .iter()
                .filter(|v| !v.is_null() && **v != "")
                .map(|v| required(v, "燃气开机台数", 4))
                .collect();
            let g = gs?
                .into_iter()
                .reduce(f64::max)
                .ok_or("日前燃气缺少开机台数")?;
            Ok(json!([coal, g]))
        }
        "cost_values" => {
            let mut map = std::collections::HashMap::new();
            for org in arr(&p["payload"]) {
                for row in arr(&org["unitCostInfoDTOList"]) {
                    let id = txt(&row["unitId"]);
                    if !id.is_empty() {
                        map.insert(id, row);
                    }
                }
            }
            let mut out = vec![];
            for id in arr(&p["unit_ids"]) {
                let row = map
                    .get(&txt(id))
                    .ok_or_else(|| format!("机组成本信息未找到机组 ID：{}", txt(id)))?;
                out.push(required(&row["priceOfDyCost"], "单位变动成本对应电价", 4)?)
            }
            Ok(json!(out))
        }
        _ => Err(format!("unknown market_payload action: {action}").into()),
    }
}
