//! Existing spot/date XML policies, shared with native package patching.
use super::{attr, cell_regex, column, numeric_xml, position, Result};
use regex::Regex;
use serde_json::Value;

fn row_range(text: &str, row: u32) -> Option<std::ops::Range<usize>> {
    Regex::new(r"(?s)<row\b[^>]*?(?:/>|>.*?</row>)")
        .unwrap()
        .find_iter(text)
        .find(|m| attr(m.as_str(), "r").as_deref() == Some(&row.to_string()))
        .map(|m| m.range())
}
fn nearest(text: &str, col: u32, row: u32) -> Option<String> {
    let mut best = 0;
    let mut style = None;
    for m in cell_regex().find_iter(text) {
        if let Some(reference) = attr(m.as_str(), "r") {
            if let Ok((r, c)) = position(&reference) {
                if c == col && r < row && r > best {
                    if let Some(s) = attr(m.as_str(), "s") {
                        best = r;
                        style = Some(s);
                    }
                }
            }
        }
    }
    style
}
pub fn extend_dates(source: &str, dates: &Value) -> Result<String> {
    let mut dates: Vec<_> = dates
        .as_object()
        .ok_or("invalid dates")?
        .iter()
        .map(|(r, v)| Ok((r.parse::<u32>()?, v)))
        .collect::<Result<_>>()?;
    dates.sort_by_key(|(r, _)| *r);
    if dates.is_empty() {
        return Ok(source.into());
    }
    let first = dates[0].0;
    for m in Regex::new(r#"<mergeCell\b[^>]*\bref="([^"]+)""#)?.captures_iter(source) {
        let (left, right) = m[1].split_once(':').unwrap_or((&m[1], &m[1]));
        let (top, col) = position(left)?;
        let (bottom, _) = position(right)?;
        if col <= 25 && dates.iter().any(|(r, _)| *r >= top && *r <= bottom) {
            return Err(format!("接续日期区域包含合并单元格 {}，停止写入。", &m[1]).into());
        }
    }
    let template = row_range(source, first.saturating_sub(1)).ok_or_else(|| {
        format!(
            "工作表 XML 未找到接续模板第 {} 行。",
            first.saturating_sub(1)
        )
    })?;
    let opening = source[template]
        .split('>')
        .next()
        .unwrap()
        .trim_end_matches('/')
        .to_string()
        + ">";
    let mut text = source.to_string();
    for (row, serial) in &dates {
        let existing = row_range(&text, *row);
        let mut raw = if let Some(r) = &existing {
            text[r.clone()].to_string()
        } else {
            Regex::new(r#"\br="\d+""#)?
                .replacen(&opening, 1, format!("r=\"{}\"", row))
                .into_owned()
                + "</row>"
        };
        if raw.ends_with("/>") {
            raw = raw.trim_end_matches("/>").to_string() + "></row>";
        }
        let reference = format!("A{}", row);
        let cell = cell_regex()
            .find_iter(&raw)
            .find(|m| attr(m.as_str(), "r").as_deref() == Some(&reference))
            .map(|m| m.range());
        let style = cell
            .as_ref()
            .and_then(|r| attr(&raw[r.clone()], "s"))
            .or_else(|| nearest(&text, 1, *row));
        let replacement = numeric_xml(&reference, serial, style.as_deref())?;
        if let Some(r) = cell {
            raw.replace_range(r, &replacement);
        } else {
            let end = raw.find('>').unwrap() + 1;
            raw.insert_str(end, &replacement);
        }
        for col in 2..26 {
            let reference = format!("{}{}", column(col), row);
            if !cell_regex()
                .find_iter(&raw)
                .any(|m| attr(m.as_str(), "r").as_deref() == Some(&reference))
            {
                if let Some(style) = nearest(&text, col, *row) {
                    raw = raw.replacen(
                        "</row>",
                        &format!("<c r=\"{}\" s=\"{}\"/></row>", reference, style),
                        1,
                    );
                }
            }
        }
        if let Some(r) = existing {
            text.replace_range(r, &raw);
        } else {
            let next = Regex::new(r"<row\b[^>]*>")?
                .find_iter(&text)
                .find(|m| {
                    attr(m.as_str(), "r")
                        .and_then(|r| r.parse::<u32>().ok())
                        .is_some_and(|r| r > *row)
                })
                .map(|m| m.start());
            let at = next
                .or_else(|| text.find("</sheetData>"))
                .ok_or("工作表 XML 缺少 sheetData 结束标记。")?;
            text.insert_str(at, &raw);
        }
    }
    let dimension = Regex::new(r#"<dimension\s+ref="([^"]+)""#)?;
    if let Some(c) = dimension.captures(&text) {
        let m = c.get(1).unwrap();
        let (left, right) = m
            .as_str()
            .split_once(':')
            .unwrap_or((m.as_str(), m.as_str()));
        let (minrow, mincol) = position(left)?;
        let (maxrow, maxcol) = position(right)?;
        let reference = format!(
            "{}{}:{}{}",
            column(mincol),
            minrow,
            column(maxcol.max(25)),
            maxrow.max(dates.last().unwrap().0)
        );
        text.replace_range(m.range(), &reference);
    }
    Ok(text)
}
pub fn spot_rows(source: &str, rows: &Value) -> Result<String> {
    let mut rows: Vec<_> = rows
        .as_object()
        .ok_or("invalid spot rows")?
        .iter()
        .map(|(r, v)| Ok((r.parse::<u32>()?, v)))
        .collect::<Result<_>>()?;
    rows.sort_by_key(|(r, _)| *r);
    let mut text = source.to_string();
    for (row, values) in rows {
        let range =
            row_range(&text, row).ok_or_else(|| format!("工作表 XML 未找到第 {} 行。", row))?;
        let raw = &text[range.clone()];
        if raw.ends_with("/>") {
            return Err(format!("工作表 XML 未找到第 {} 行。", row).into());
        }
        let start = raw.find('>').unwrap() + 1;
        let end = raw.rfind("</row>").unwrap();
        let mut opening = raw[..start].to_string();
        let mut content = raw[start..end].to_string();
        if opening.contains("spans=\"") {
            opening = Regex::new(r#"spans="[^"]*""#)?
                .replacen(&opening, 1, "spans=\"1:25\"")
                .into_owned();
        } else {
            opening = opening.trim_end_matches('>').to_string() + " spans=\"1:25\">";
        }
        for (offset, value) in values
            .as_array()
            .ok_or("invalid spot values")?
            .iter()
            .enumerate()
        {
            let col = offset as u32 + 2;
            let reference = format!("{}{}", column(col), row);
            let cell = cell_regex()
                .find_iter(&content)
                .find(|m| attr(m.as_str(), "r").as_deref() == Some(&reference))
                .map(|m| m.range());
            let style = if let Some(r) = &cell {
                attr(&content[r.clone()], "s")
            } else {
                nearest(&text, col, row)
            };
            let replacement = numeric_xml(&reference, value, style.as_deref())?;
            if let Some(r) = cell {
                content.replace_range(r, &replacement);
            } else {
                content.push_str(&replacement);
            }
        }
        text.replace_range(range, &format!("{}{}{}</row>", opening, content, ""));
    }
    Ok(text)
}
