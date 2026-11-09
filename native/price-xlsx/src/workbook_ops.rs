//! ZIP/XML workbook operations. Rendering/style objects remain Python adapters.
#[path = "workbook_patch.rs"]
mod patch;
#[path = "workbook_transaction.rs"]
mod transaction;
use quick_xml::{events::Event, Reader};
use regex::Regex;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::{Read, Write};
use std::path::Path;
pub use transaction::transaction_stdio;
use zip::{write::SimpleFileOptions, ZipArchive, ZipWriter};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Default, Clone)]
struct Node {
    name: String,
    attrs: BTreeMap<String, String>,
    text: String,
    children: Vec<Node>,
}
impl Node {
    fn attr(&self, k: &str) -> &str {
        self.attrs
            .get(k)
            .or_else(|| {
                if k.contains(':') {
                    let local = k.rsplit(':').next().unwrap();
                    self.attrs
                        .iter()
                        .find(|(key, _)| key.rsplit(':').next() == Some(local))
                        .map(|(_, v)| v)
                } else {
                    None
                }
            })
            .map(String::as_str)
            .unwrap_or("")
    }
    fn child(&self, name: &str) -> Option<&Node> {
        self.children.iter().find(|x| x.name == name)
    }
    fn content(&self) -> String {
        let mut s = self.text.clone();
        for c in &self.children {
            if c.name != "rPh" {
                s.push_str(&c.content());
            }
        }
        s
    }
}
fn parse(xml: &str) -> Result<Node> {
    let mut reader = Reader::from_str(xml);
    let mut stack = vec![Node::default()];
    loop {
        match reader.read_event()? {
            Event::Start(e) | Event::Empty(e) => {
                let empty =
                    xml.as_bytes().get(reader.buffer_position() as usize - 2) == Some(&b'/');
                let name = String::from_utf8(e.local_name().as_ref().to_vec())?;
                let mut n = Node {
                    name,
                    ..Default::default()
                };
                for a in e.attributes() {
                    let a = a?;
                    n.attrs.insert(
                        String::from_utf8(a.key.as_ref().to_vec())?,
                        a.decoded_and_normalized_value(
                            quick_xml::XmlVersion::Implicit1_0,
                            reader.decoder(),
                        )?
                        .into_owned(),
                    );
                }
                if empty {
                    stack.last_mut().unwrap().children.push(n);
                } else {
                    stack.push(n);
                }
            }
            Event::End(_) => {
                if stack.len() < 2 {
                    return Err("unbalanced workbook XML".into());
                }
                let n = stack.pop().unwrap();
                stack.last_mut().unwrap().children.push(n);
            }
            Event::Text(t) => stack
                .last_mut()
                .unwrap()
                .text
                .push_str(&t.xml_content(quick_xml::XmlVersion::Implicit1_0)?),
            Event::CData(t) => stack
                .last_mut()
                .unwrap()
                .text
                .push_str(&t.xml_content(quick_xml::XmlVersion::Implicit1_0)?),
            Event::GeneralRef(t) => {
                let name = t.decode()?;
                let escaped = format!("&{};", name);
                stack
                    .last_mut()
                    .unwrap()
                    .text
                    .push_str(&quick_xml::escape::unescape(&escaped)?);
            }
            Event::Eof => break,
            _ => {}
        }
    }
    if stack.len() != 1 {
        return Err("unclosed workbook XML".into());
    }
    stack
        .pop()
        .unwrap()
        .children
        .into_iter()
        .next()
        .ok_or_else(|| "empty workbook XML".into())
}
fn read_part(zip: &mut ZipArchive<File>, name: &str) -> Result<String> {
    let mut s = String::new();
    zip.by_name(name)?.read_to_string(&mut s)?;
    Ok(s)
}
fn optional_part(zip: &mut ZipArchive<File>, name: &str) -> Result<Option<String>> {
    match zip.by_name(name) {
        Ok(mut member) => {
            let mut text = String::new();
            member.read_to_string(&mut text)?;
            Ok(Some(text))
        }
        Err(zip::result::ZipError::FileNotFound) => Ok(None),
        Err(error) => Err(error.into()),
    }
}
fn paths(zip: &mut ZipArchive<File>) -> Result<(Value, bool)> {
    let book = parse(&read_part(zip, "xl/workbook.xml")?)?;
    let rels = parse(&read_part(zip, "xl/_rels/workbook.xml.rels")?)?;
    let mut result = Vec::new();
    if let Some(sheets) = book.child("sheets") {
        for sheet in &sheets.children {
            let relation = rels
                .children
                .iter()
                .find(|r| r.attr("Id") == sheet.attr("r:id"));
            if let Some(r) = relation {
                if r.attr("Type").contains("chartsheet") {
                    continue;
                }
                let target = r.attr("Target").trim_start_matches('/');
                let path = if target.starts_with("xl/") {
                    target.to_owned()
                } else {
                    format!("xl/{}", target)
                };
                result.push(json!({"name":sheet.attr("name"),"path":path}));
            }
        }
    }
    let epoch1904 = book
        .child("workbookPr")
        .is_some_and(|p| ["1", "true"].contains(&p.attr("date1904")));
    Ok((json!(result), epoch1904))
}
fn position(s: &str) -> Result<(u32, u32)> {
    let mut col = 0u32;
    let mut row = String::new();
    for c in s.chars() {
        if c.is_ascii_alphabetic() {
            col = col * 26 + c.to_ascii_uppercase() as u32 - 'A' as u32 + 1
        } else {
            row.push(c);
        }
    }
    Ok((row.parse()?, col))
}
fn column(mut n: u32) -> String {
    let mut out = String::new();
    while n > 0 {
        n -= 1;
        out.insert(0, (b'A' + (n % 26) as u8) as char);
        n /= 26;
    }
    out
}
fn translate(formula: &str, from: &str, to: &str) -> Result<String> {
    let (fr, fc) = position(from)?;
    let (tr, tc) = position(to)?;
    let expression = Regex::new(
        r"(?i)\$?[A-Z]{1,3}:\$?[A-Z]{1,3}|\$?[1-9][0-9]*:\$?[1-9][0-9]*|\$?[A-Z]{1,3}\$?[1-9][0-9]*",
    )?;
    // Quoted sheet names, strings and structured-reference brackets are names,
    // not A1 references. Keep them byte-identical while translating operands.
    let strings = Regex::new(r#""(?:[^"]|"")*"|'(?:[^']|'')*'"#)?;
    let mut literals: Vec<_> = strings.find_iter(formula).map(|m| m.range()).collect();
    let mut bracket_start = None;
    let mut depth = 0;
    for (index, ch) in formula.char_indices() {
        if literals.iter().any(|r| r.contains(&index)) {
            continue;
        }
        if ch == '[' {
            if depth == 0 {
                bracket_start = Some(index)
            }
            depth += 1;
        } else if ch == ']' && depth > 0 {
            depth -= 1;
            if depth == 0 {
                literals.push(bracket_start.take().unwrap()..index + 1);
            }
        }
    }
    let shifted_column = |part: &str| -> Result<String> {
        let absolute = part.starts_with('$');
        if absolute {
            return Ok(part.to_string());
        }
        let (_, n) = position(&format!("{}1", part))?;
        let n = n as i64 + tc as i64 - fc as i64;
        if n < 1 {
            return Err("Formula out of range".into());
        }
        Ok(column(n as u32))
    };
    let shifted_row = |part: &str| -> Result<String> {
        let absolute = part.starts_with('$');
        let n = part.trim_start_matches('$').parse::<i64>()?;
        let n = if absolute {
            n
        } else {
            n + tr as i64 - fr as i64
        };
        if n < 1 {
            return Err("Formula out of range".into());
        }
        Ok(format!("{}{}", if absolute { "$" } else { "" }, n))
    };
    let mut output = String::new();
    let mut end = 0;
    for m in expression.find_iter(formula) {
        if literals.iter().any(|r| r.contains(&m.start())) {
            continue;
        }
        let boundary = |c: char| c.is_alphanumeric() || c == '_' || c == '.';
        if formula[..m.start()]
            .chars()
            .next_back()
            .is_some_and(boundary)
            || formula[m.end()..]
                .chars()
                .next()
                .is_some_and(|c| boundary(c) || c == '(' || c == '!')
        {
            continue;
        }
        let reference = m.as_str();
        let translated = if let Some((left, right)) = reference.split_once(':') {
            if left.chars().any(|c| c.is_ascii_alphabetic()) {
                format!("{}:{}", shifted_column(left)?, shifted_column(right)?)
            } else {
                format!("{}:{}", shifted_row(left)?, shifted_row(right)?)
            }
        } else {
            let index = reference.find(|c: char| c.is_ascii_digit()).unwrap();
            let mut col_end = index;
            if reference[..index].ends_with('$') {
                col_end -= 1;
            }
            format!(
                "{}{}",
                shifted_column(&reference[..col_end])?,
                shifted_row(&reference[col_end..])?
            )
        };
        output.push_str(&formula[end..m.start()]);
        output.push_str(&translated);
        end = m.end();
    }
    output.push_str(&formula[end..]);
    Ok(output)
}
fn raw_value(cell: &Node, strings: &[String]) -> Result<Value> {
    let raw = cell.child("v").map(|v| v.text.as_str()).unwrap_or("");
    Ok(match cell.attr("t") {
        "inlineStr" => cell
            .child("is")
            .map(|n| json!(n.content()))
            .unwrap_or(Value::Null),
        "s" => {
            if raw.is_empty() {
                Value::Null
            } else {
                json!(strings
                    .get(raw.parse::<usize>()?)
                    .ok_or("shared string index out of range")?)
            }
        }
        "b" => {
            if raw.is_empty() {
                Value::Null
            } else {
                json!(raw.parse::<i32>()? != 0)
            }
        }
        "str" | "e" | "d" => {
            if raw.is_empty() {
                Value::Null
            } else {
                json!(raw)
            }
        }
        _ => {
            if raw.is_empty() {
                Value::Null
            } else if !raw.contains(['.', 'e', 'E']) {
                match raw.parse::<i64>() {
                    Ok(value) => json!(value),
                    Err(_) => {
                        if !Regex::new(r"^[+-]?[0-9]+$")?.is_match(raw) {
                            return Err("invalid integer cell value".into());
                        }
                        json!({"number_text":raw,"integer":true})
                    }
                }
            } else {
                let value = raw.parse::<f64>()?;
                if value.is_finite() {
                    json!(value)
                } else {
                    json!({"number_text":raw,"integer":false})
                }
            }
        }
    })
}
fn inspect(payload: &Value) -> Result<Value> {
    let mut zip = ZipArchive::new(File::open(payload["path"].as_str().ok_or("missing path")?)?)?;
    let (descriptors, epoch1904) = paths(&mut zip)?;
    let styles = optional_part(&mut zip, "xl/styles.xml")?.unwrap_or_default();
    let style_count = if styles.is_empty() {
        1
    } else {
        parse(&styles)?
            .child("cellXfs")
            .map(|x| x.children.iter().filter(|n| n.name == "xf").count())
            .unwrap_or(0)
            .max(1)
    };
    let strings = match optional_part(&mut zip, "xl/sharedStrings.xml")? {
        Some(s) => parse(&s)?
            .children
            .iter()
            .map(Node::content)
            .collect::<Vec<_>>(),
        None => Vec::new(),
    };
    let selected = payload["selected_sheets"]
        .as_array()
        .ok_or("missing selected sheets")?;
    let columns: BTreeSet<u64> = payload["columns"]
        .as_array()
        .ok_or("missing columns")?
        .iter()
        .filter_map(Value::as_u64)
        .collect();
    let mut out = Vec::new();
    for descriptor in descriptors.as_array().unwrap() {
        let name = descriptor["name"].as_str().unwrap();
        let part = descriptor["path"].as_str().unwrap();
        let source = read_part(&mut zip, part).map_err(|_| "工作表部件缺失")?;
        let sheet = parse(&source)?;
        let merges: Vec<_> = sheet
            .child("mergeCells")
            .into_iter()
            .flat_map(|n| n.children.iter())
            .map(|m| json!(m.attr("ref")))
            .collect();
        if !selected.iter().any(|n| n == name) {
            if !merges.is_empty() {
                return Ok(json!({"unsupported":"非月份工作表含合并样式"}));
            }
            continue;
        }
        let mut cells = Vec::new();
        let mut cached = Vec::new();
        let mut row_styles = Vec::new();
        let mut shared: BTreeMap<String, (String, String)> = BTreeMap::new();
        let mut max_row = 0;
        let mut row_counter = 0;
        if let Some(data) = sheet.child("sheetData") {
            for row in &data.children {
                row_counter = if row.attr("r").is_empty() {
                    row_counter + 1
                } else {
                    row.attr("r").parse()?
                };
                let mut col_counter = 0;
                for cell in row.children.iter().filter(|c| c.name == "c") {
                    let (r, c) = if cell.attr("r").is_empty() {
                        (row_counter, col_counter + 1)
                    } else {
                        position(cell.attr("r"))?
                    };
                    col_counter = c;
                    max_row = max_row.max(r);
                    let coordinate = format!("{}{}", column(c), r);
                    let style = match cell.attrs.get("s") {
                        None => 0,
                        Some(raw) => {
                            let index = raw.parse::<u32>().map_err(|_| {
                                format!(
                                    "工作表 {} 的单元格 {} 样式编号无效：{}",
                                    name, coordinate, raw
                                )
                            })?;
                            if index as usize >= style_count {
                                return Err(format!(
                                    "工作表 {} 的单元格 {} 样式编号无效：{}",
                                    name, coordinate, raw
                                )
                                .into());
                            }
                            index
                        }
                    };
                    row_styles.push(json!([r, c, style]));
                    let raw = raw_value(cell, &strings)?;
                    let mut value = raw.clone();
                    let mut kind = cell.attr("t").to_string();
                    if let Some(f) = cell.child("f") {
                        kind = "f".into();
                        value = if f.attr("t") == "shared" {
                            if !f.text.is_empty() {
                                shared.insert(
                                    f.attr("si").into(),
                                    (coordinate.clone(), f.text.clone()),
                                );
                                json!(format!("={}", f.text))
                            } else if let Some((origin, text)) = shared.get(f.attr("si")) {
                                json!(format!("={}", translate(text, origin, &coordinate)?))
                            } else {
                                json!("=")
                            }
                        } else if ["array", "dataTable"].contains(&f.attr("t")) {
                            json!({"formula_type":f.attr("t"),"text":format!("={}",f.text),"attributes":f.attrs})
                        } else {
                            json!(format!("={}", f.text))
                        };
                    }
                    let include = columns.contains(&(c as u64))
                        && (payload["requests"].is_null()
                            || payload["requests"][name]
                                .as_array()
                                .is_some_and(|rs| rs.iter().any(|v| v == &json!([r, c]))));
                    if include {
                        cells.push(
                            json!({"row":r,"column":c,"style":style,"value":value,"kind":kind}),
                        );
                    }
                    if payload["capture_cache"] != false && c == 1 {
                        cached.push(json!({"row":r,"column":c,"style":style,"value":raw,"kind":cell.attr("t")}));
                    }
                }
            }
        }
        out.push(json!({"name":name,"cells":cells,"cached":cached,"max_row":max_row,"merges":merges,"row_styles":row_styles}));
    }
    Ok(json!({"epoch1904":epoch1904,"styles_xml":styles,"sheets":out}))
}

fn cell_regex() -> Regex {
    Regex::new(r#"(?s)<c\b[^>]*?(?:/>|>.*?</c>)"#).unwrap()
}
fn attr(xml: &str, key: &str) -> Option<String> {
    Regex::new(&format!(r#"\b{}="([^"]*)""#, regex::escape(key)))
        .unwrap()
        .captures(xml)
        .map(|c| c[1].to_string())
}
fn numeric_xml(reference: &str, value: &Value, style: Option<&str>) -> Result<String> {
    if value.is_object() {
        let explicit = value
            .get("style_id")
            .and_then(Value::as_u64)
            .map(|s| s.to_string());
        let style = explicit.as_deref().or(style);
        let style = style.map(|s| format!(" s=\"{}\"", s)).unwrap_or_default();
        let raw = value["value"]
            .as_str()
            .map(str::to_string)
            .unwrap_or_else(|| value["value"].to_string());
        let opening = format!("<c r=\"{}\"{}", reference, style);
        return Ok(match value["kind"].as_str().ok_or("missing cell kind")? {
            "n" => {
                if !raw.parse::<f64>()?.is_finite() {
                    return Err("数值必须有限".into());
                }
                format!("{}><v>{}</v></c>", opening, raw)
            }
            "b" => format!(
                "{} t=\"b\"><v>{}</v></c>",
                opening,
                if value["value"] == true || value["value"] == 1 || value["value"] == "1" {
                    1
                } else {
                    0
                }
            ),
            "inlineStr" => format!(
                "{} t=\"inlineStr\"><is><t{}>{}</t></is></c>",
                opening,
                if raw.trim() != raw {
                    " xml:space=\"preserve\""
                } else {
                    ""
                },
                quick_xml::escape::partial_escape(&raw)
            ),
            "f" => format!(
                "{}><f>{}</f><v></v></c>",
                opening,
                quick_xml::escape::partial_escape(raw.strip_prefix('=').unwrap_or(&raw))
            ),
            "null" => format!("{}/>", opening),
            _ => return Err("unsupported typed workbook cell".into()),
        });
    }
    // Python supplies .15g strings, preserving its exact numeric lexical policy.
    let number = value
        .as_str()
        .map(str::to_string)
        .unwrap_or_else(|| value.to_string());
    if !number.parse::<f64>()?.is_finite() {
        return Err("数值必须有限".into());
    }
    Ok(format!(
        "<c r=\"{}\"{}><v>{}</v></c>",
        reference,
        style.map(|s| format!(" s=\"{}\"", s)).unwrap_or_default(),
        number
    ))
}
fn expand_shared(source: &str, changes: &serde_json::Map<String, Value>) -> Result<String> {
    let regex = cell_regex();
    let mut masters = BTreeMap::new();
    for m in regex.find_iter(source) {
        let cell = m.as_str();
        let node = parse(cell)?;
        if let Some(f) = node.child("f") {
            if f.attr("t") == "shared" && !f.text.is_empty() {
                masters.insert(
                    f.attr("si").to_string(),
                    (node.attr("r").to_string(), f.text.clone()),
                );
            }
        }
    }
    let replaced: BTreeSet<_> = masters
        .iter()
        .filter(|(_, (origin, _))| changes.contains_key(origin))
        .map(|(si, _)| si.clone())
        .collect();
    if replaced.is_empty() {
        return Ok(source.to_string());
    }
    let mut out = String::new();
    let mut end = 0;
    let formula_re = Regex::new(r"(?s)<f\b[^>]*?(?:/>|>.*?</f>)")?;
    for m in regex.find_iter(source) {
        let node = parse(m.as_str())?;
        if let Some(f) = node.child("f") {
            if f.attr("t") == "shared" && replaced.contains(f.attr("si")) {
                let (origin, text) = &masters[f.attr("si")];
                let formula = translate(text, origin, node.attr("r"))?;
                let replacement = format!("<f>{}</f>", quick_xml::escape::partial_escape(&formula));
                out.push_str(&source[end..m.start()]);
                out.push_str(&formula_re.replace(m.as_str(), regex::NoExpand(&replacement)));
                end = m.end();
            }
        }
    }
    out.push_str(&source[end..]);
    Ok(out)
}
fn sparse(source: &str, changes: &Value) -> Result<String> {
    let changes = changes.as_object().ok_or("invalid cell changes")?;
    if changes.is_empty() {
        return Ok(source.into());
    }
    let source = expand_shared(source, changes)?;
    let typed = changes.values().any(Value::is_object);
    let mut result = String::new();
    let mut end = 0;
    let mut replaced = BTreeSet::new();
    let regex = cell_regex();
    for m in regex.find_iter(&source) {
        let cell = m.as_str();
        let reference = attr(cell, "r").ok_or("target cell lacks coordinate")?;
        if let Some(value) = changes.get(&reference) {
            if replaced.insert(reference.clone()) {
                result.push_str(&source[end..m.start()]);
                result.push_str(&numeric_xml(&reference, value, attr(cell, "s").as_deref())?);
                end = m.end();
            }
        }
    }
    result.push_str(&source[end..]);
    for (reference, value) in changes {
        if replaced.contains(reference) {
            continue;
        }
        let (row, col) = position(reference)?;
        let rows = Regex::new(r"(?s)<row\b[^>]*?(?:/>|>.*?</row>)")?;
        let existing = rows
            .find_iter(&result)
            .find(|m| attr(m.as_str(), "r").as_deref() == Some(&row.to_string()))
            .map(|m| m.range());
        if existing.is_none() && typed {
            if result.contains("<sheetData/>") {
                result = result.replacen("<sheetData/>", "<sheetData></sheetData>", 1);
            }
            let at = rows
                .find_iter(&result)
                .find(|m| {
                    attr(m.as_str(), "r")
                        .and_then(|r| r.parse::<u32>().ok())
                        .is_some_and(|r| r > row)
                })
                .map(|m| m.start())
                .or_else(|| result.find("</sheetData>"))
                .ok_or("工作表 XML 缺少 sheetData 结束标记。")?;
            result.insert_str(at, &format!("<row r=\"{}\"></row>", row));
        }
        let m = rows
            .find_iter(&result)
            .find(|m| attr(m.as_str(), "r").as_deref() == Some(&row.to_string()))
            .ok_or_else(|| format!("模板缺少目标行 {}，停止写入。", row))?;
        let raw = m.as_str();
        if raw.ends_with("/>") {
            if typed {
                let replacement = raw.trim_end_matches("/>").to_string()
                    + ">"
                    + &numeric_xml(reference, value, None)?
                    + "</row>";
                result.replace_range(m.range(), &replacement);
                continue;
            }
            return Err(format!("模板缺少目标行 {}，停止写入。", row).into());
        }
        let start = raw.find('>').unwrap() + 1;
        let stop = raw.rfind("</row>").unwrap();
        let mut insertion = stop;
        for c in regex.find_iter(&raw[start..stop]) {
            if let Some(r) = attr(c.as_str(), "r") {
                if position(&r)?.1 > col {
                    insertion = start + c.start();
                    break;
                }
            }
        }
        let text = format!(
            "{}{}{}",
            &raw[..insertion],
            numeric_xml(reference, value, None)?,
            &raw[insertion..]
        );
        result.replace_range(m.range(), &text);
    }
    // Cell removal equality pins all non-target bytes, formulas and dimensions.
    let omit = |text: &str| -> String {
        regex
            .replace_all(text, |caps: &regex::Captures| {
                if attr(&caps[0], "r").is_some_and(|r| changes.contains_key(&r)) {
                    String::new()
                } else {
                    caps[0].to_string()
                }
            })
            .into_owned()
    };
    if !typed && omit(&source) != omit(&result) {
        return Err("工作表非目标单元格或公式发生变化，停止写入。".into());
    }
    if typed {
        let dimension = Regex::new(r#"<dimension\s+ref="([^"]+)""#)?;
        if let Some(c) = dimension.captures(&result) {
            let m = c.get(1).unwrap();
            let (start, end) = m
                .as_str()
                .split_once(':')
                .unwrap_or((m.as_str(), m.as_str()));
            let (mut minr, mut minc) = position(start)?;
            let (mut maxr, mut maxc) = position(end)?;
            for reference in changes.keys() {
                let (r, c) = position(reference)?;
                minr = minr.min(r);
                minc = minc.min(c);
                maxr = maxr.max(r);
                maxc = maxc.max(c);
            }
            let reference = if minr == maxr && minc == maxc {
                format!("{}{}", column(minc), minr)
            } else {
                format!("{}{}:{}{}", column(minc), minr, column(maxc), maxr)
            };
            result.replace_range(m.range(), &reference);
        }
    }
    Ok(result)
}
fn recalculate(source: &str) -> Result<String> {
    let regex = Regex::new(r"<calcPr\b[^>]*/>")?;
    if let Some(m) = regex.find(source) {
        let mut tag = m.as_str().to_string();
        for key in ["fullCalcOnLoad", "forceFullCalc", "calcOnSave"] {
            tag = Regex::new(&format!(r#"\s{}="[^"]*""#, key))?
                .replace_all(&tag, "")
                .into_owned();
            tag = tag.trim_end_matches("/>").to_string() + &format!(" {}=\"1\"/>", key);
        }
        Ok(format!(
            "{}{}{}",
            &source[..m.start()],
            tag,
            &source[m.end()..]
        ))
    } else {
        Ok(source.replace(
            "</workbook>",
            "<calcPr fullCalcOnLoad=\"1\" forceFullCalc=\"1\" calcOnSave=\"1\"/></workbook>",
        ))
    }
}
fn append_rows(source: &str, groups: &Value) -> Result<String> {
    let mut result = source.to_string();
    let mut appended = String::new();
    let mut last = None;
    let row_re = Regex::new(r"(?s)<row\b[^>]*>.*?</row>")?;
    for row in groups.as_array().ok_or("invalid appended rows")? {
        let from = row["source_row"].as_u64().ok_or("invalid source row")?;
        let to = row["target_row"].as_u64().ok_or("invalid target row")?;
        let template = row_re
            .find_iter(source)
            .find(|m| attr(m.as_str(), "r").as_deref() == Some(&from.to_string()))
            .ok_or_else(|| format!("工作表 XML 未找到模板第 {} 行。", from))?;
        let mut text = template.as_str().to_string();
        text = Regex::new(&format!(r#"(<row\b[^>]*\br="){}("[^>]*>)"#, from))?
            .replacen(&text, 1, format!("${{1}}{}${{2}}", to))
            .into_owned();
        text = Regex::new(&format!(r#"([A-Z]+){}(\")"#, from))?
            .replace_all(&text, format!("${{1}}{}${{2}}", to))
            .into_owned();
        // Existing required template cells only; sparse insertion isn't allowed.
        for reference in row["changes"]
            .as_object()
            .ok_or("invalid appended changes")?
            .keys()
        {
            if !cell_regex()
                .find_iter(&text)
                .any(|m| attr(m.as_str(), "r").as_deref() == Some(reference))
            {
                return Err(format!("模板行缺少单元格 {}。", reference).into());
            }
        }
        appended.push_str(&sparse(&text, &row["changes"])?);
        last = Some(to);
    }
    if !source.contains("</sheetData>") {
        return Err("工作表 XML 缺少 sheetData 结束标记。".into());
    }
    result = result.replacen("</sheetData>", &format!("{}</sheetData>", appended), 1);
    if let Some(last) = last {
        let dimension = Regex::new(r#"<dimension\s+ref="([^"]+)""#)?;
        let c = dimension
            .captures(&result)
            .ok_or("工作表 XML 缺少 dimension。")?;
        let m = c.get(1).unwrap();
        let replacement = Regex::new(r"\d+$")?
            .replace(m.as_str(), last.to_string())
            .into_owned();
        result.replace_range(m.range(), &replacement);
    }
    Ok(result)
}
fn verify_package(payload: &Value) -> Result<Value> {
    let original = payload["original"].as_str().ok_or("missing original")?;
    let mut a = ZipArchive::new(File::open(original)?)?;
    let mut b = ZipArchive::new(File::open(
        payload["output"].as_str().ok_or("missing output")?,
    )?)?;
    let an: Vec<_> = a.file_names().map(str::to_string).collect();
    let bn: Vec<_> = b.file_names().map(str::to_string).collect();
    if an != bn {
        return Err(format!(
            "XLSX 包结构发生变化：{}",
            Path::new(original).file_name().unwrap().to_string_lossy()
        )
        .into());
    }
    for i in 0..a.len() {
        let mut aa = a.by_index(i)?;
        if payload["changed_parts"]
            .as_array()
            .is_some_and(|ps| ps.iter().any(|p| p == aa.name()))
        {
            continue;
        }
        let mut bb = b.by_index(i)?;
        let mut av = Vec::new();
        let mut bv = Vec::new();
        aa.read_to_end(&mut av)?;
        bb.read_to_end(&mut bv)?;
        if av != bv {
            return Err(format!("XLSX 非目标部件被改动：{} / {}", original, aa.name()).into());
        }
    }
    Ok(json!({"verified":true}))
}
fn patch_package(payload: &Value) -> Result<Value> {
    let original = payload["original"].as_str().ok_or("missing original")?;
    let output = payload["output"].as_str().ok_or("missing output")?;
    if Path::new(original).canonicalize()? == Path::new(output).canonicalize().unwrap_or_default() {
        return Err("不能覆盖输入工作簿".into());
    }
    let mut source = ZipArchive::new(File::open(original)?)?;
    let (sheet_paths, _) = paths(&mut source)?;
    let mut changes = BTreeMap::new();
    let mut append = BTreeMap::new();
    let resolve = |values: &Value| -> Result<BTreeMap<String, Value>> {
        let mut result = BTreeMap::new();
        for (name, value) in values.as_object().into_iter().flat_map(|m| m.iter()) {
            let part = sheet_paths
                .as_array()
                .unwrap()
                .iter()
                .find(|s| s["name"] == *name)
                .ok_or_else(|| format!("XLSX 包内未找到工作表：{}", name))?["path"]
                .as_str()
                .unwrap();
            result.insert(part.to_string(), value.clone());
        }
        Ok(result)
    };
    let spot_rows = resolve(&payload["spot_rows"])?;
    let dates = resolve(&payload["date_extensions"])?;
    let replacements = payload["replace_parts"]
        .as_object()
        .cloned()
        .unwrap_or_default();
    for name in replacements.keys() {
        if !source.file_names().any(|n| n == name) {
            return Err(format!("XLSX 部件缺失：{}", name).into());
        }
    }
    for (name, value) in payload["changes"]
        .as_object()
        .into_iter()
        .flat_map(|m| m.iter())
    {
        let path = sheet_paths
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["name"] == *name)
            .ok_or_else(|| format!("XLSX 包内未找到工作表：{}", name))?["path"]
            .as_str()
            .unwrap()
            .to_string();
        changes.insert(path, value.clone());
    }
    for (name, value) in payload["append"]
        .as_object()
        .into_iter()
        .flat_map(|m| m.iter())
    {
        let path = sheet_paths
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["name"] == *name)
            .ok_or_else(|| format!("XLSX 包内未找到工作表：{}", name))?["path"]
            .as_str()
            .unwrap()
            .to_string();
        append.insert(path, value.clone());
    }
    if changes.is_empty()
        && append.is_empty()
        && spot_rows.is_empty()
        && dates.is_empty()
        && replacements.is_empty()
        && payload["recalculate"] != true
    {
        std::fs::copy(original, output)?;
        return Ok(json!({"changed_parts":[]}));
    }
    let mut writer = ZipWriter::new(File::create(output)?);
    writer.set_raw_comment(source.comment().to_vec().into_boxed_slice())?;
    let mut changed = Vec::new();
    for i in 0..source.len() {
        let mut member = source.by_index(i)?;
        let name = member.name().to_string();
        if changes.contains_key(&name)
            || append.contains_key(&name)
            || spot_rows.contains_key(&name)
            || dates.contains_key(&name)
            || replacements.contains_key(&name)
            || (name == "xl/workbook.xml" && payload["recalculate"] == true)
        {
            let mut text = String::new();
            member.read_to_string(&mut text)?;
            if let Some(replacement) = replacements.get(&name) {
                text = replacement
                    .as_str()
                    .ok_or("replacement part must be XML text")?
                    .to_string();
            }
            if let Some(values) = dates.get(&name) {
                text = patch::extend_dates(&text, values)?;
            }
            if let Some(values) = spot_rows.get(&name) {
                text = patch::spot_rows(&text, values)?;
            }
            if let Some(rows) = append.get(&name) {
                text = append_rows(&text, rows)?;
            }
            if let Some(values) = changes.get(&name) {
                text = sparse(&text, values)?;
            }
            if name == "xl/workbook.xml" && payload["recalculate"] == true {
                text = recalculate(&text)?;
            }
            let options = SimpleFileOptions::default()
                .compression_method(member.compression())
                .last_modified_time(member.last_modified().unwrap_or_default())
                .unix_permissions(member.unix_mode().unwrap_or(0o600));
            writer.start_file(&name, options)?;
            writer.write_all(text.as_bytes())?;
            changed.push(json!(name));
        } else {
            writer.raw_copy_file(member)?;
        }
    }
    writer.finish()?;
    verify_package(&json!({"original":original,"output":output,"changed_parts":changed}))?;
    Ok(json!({"changed_parts":changed}))
}
pub fn execute(payload: &Value) -> Result<Value> {
    match payload["action"].as_str().unwrap_or("") {
        "capabilities" => Ok(json!({"transaction":true,"inspection":true,"patch_package":true})),
        "fingerprint" => {
            transaction::fingerprint(Path::new(payload["path"].as_str().ok_or("missing path")?))
        }
        "sheet_paths" => {
            let mut zip =
                ZipArchive::new(File::open(payload["path"].as_str().ok_or("missing path")?)?)?;
            let (paths, epoch) = paths(&mut zip)?;
            Ok(json!({"sheets":paths,"epoch1904":epoch}))
        }
        "inspect" => inspect(payload),
        "patch_sparse_xml" => Ok(
            json!({"xml":sparse(payload["xml"].as_str().ok_or("missing xml")?,&payload["changes"])?}),
        ),
        "extend_dates_xml" => Ok(
            json!({"xml":patch::extend_dates(payload["xml"].as_str().ok_or("missing xml")?,&payload["dates"])?}),
        ),
        "patch_spot_rows" => Ok(
            json!({"xml":patch::spot_rows(payload["xml"].as_str().ok_or("missing xml")?,&payload["rows"])?}),
        ),
        "recalculate_xml" => {
            Ok(json!({"xml":recalculate(payload["xml"].as_str().ok_or("missing xml")?)?}))
        }
        "append_rows_xml" => Ok(
            json!({"xml":append_rows(payload["xml"].as_str().ok_or("missing xml")?,&payload["rows"])?}),
        ),
        "patch_package" => patch_package(payload),
        "verify_package" => verify_package(payload),
        other => Err(format!("unknown workbook action: {}", other).into()),
    }
}
