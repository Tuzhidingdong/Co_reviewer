#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Co_Reviewer Unified CLI Tool (Worker Executor)
专为 OpenCode Worker 设计的零试草 Word 底稿体检、媒体移植与原生审阅修订注入器
"""
import os
import re
import sys
import json
import shutil
import zipfile
import argparse
from datetime import datetime
from lxml import etree

NS_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS_A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
NS = {'w': NS_W, 'r': NS_R, 'a': NS_A}

def get_p_text(p):
    texts = []
    for node in p.iter():
        if node.tag == f"{{{NS_W}}}del":
            continue
        if node.tag == f"{{{NS_W}}}t" and node.text:
            texts.append(node.text)
    return "".join(texts)

def inspect_single_docx(docx_path, author_kw=None):
    res = {"file": docx_path, "media_count": 0, "comments": [], "flags": [], "captions": []}
    if not os.path.exists(docx_path):
        return res

    with zipfile.ZipFile(docx_path, 'r') as z:
        res["media_count"] = len([f for f in z.namelist() if f.startswith('word/media/')])
        doc_xml = etree.fromstring(z.read('word/document.xml'))
        comments_map = {}

        if 'word/comments.xml' in z.namelist():
            comm_xml = etree.fromstring(z.read('word/comments.xml'))
            for c in comm_xml.xpath('//w:comment', namespaces=NS):
                cid = c.get(f"{{{NS_W}}}id")
                c_author = c.get(f"{{{NS_W}}}author", "")
                c_text = "".join(c.xpath('.//w:t/text()', namespaces=NS)).strip()
                comments_map[cid] = {"id": cid, "author": c_author, "text": c_text}

        flag_regex = re.compile(
            r'(Fig\.s*S[xX]+|Figure\s*S[xX]+|Page\s*[xX]+|Lines?\s*[xX]+|\[x+\]|\[XX+\]|\[Refs\.[^\]]+\]|[\u4e00-\u9fa5]{2,})'
        )
        caption_regex = re.compile(r'^(Fig\.|Figure|Table)\s*(S?\d+|S?[xX]+)', re.I)

        for idx, p in enumerate(doc_xml.xpath('//w:body/w:p', namespaces=NS)):
            p_text = get_p_text(p).strip()
            has_drawing = len(p.xpath('.//w:drawing', namespaces=NS)) > 0

            c_ids = set(p.xpath('.//w:commentRangeStart/@w:id', namespaces=NS) +
                        p.xpath('.//w:commentReference/@w:id', namespaces=NS))
            for cid in c_ids:
                if cid in comments_map:
                    c_obj = comments_map[cid]
                    if not author_kw or (author_kw in c_obj["text"] or author_kw in c_obj["author"]):
                        res["comments"].append({
                            "id": cid, "p_idx": idx,
                            "comment": c_obj["text"],
                            "anchor_paragraph": p_text
                        })

            if not p_text and not has_drawing:
                continue

            found_flags = flag_regex.findall(p_text)
            if found_flags:
                res["flags"].append({
                    "p_idx": idx,
                    "matches": list(set(found_flags)),
                    "exact_paragraph": p_text
                })

            if caption_regex.match(p_text) or has_drawing:
                res["captions"].append({
                    "p_idx": idx,
                    "has_image": has_drawing,
                    "text": p_text[:120]
                })
    return res

def cmd_inspect(args):
    files = [f for f in [args.rev, args.man, args.si] if f]
    reports = [inspect_single_docx(f, args.author) for f in files]

    md_lines = ["# Co_Reviewer Workspace Digest (Low-Token Summary)\n"]
    for rep in reports:
        md_lines.append(f"## File: `{rep['file']}` (Media files: {rep['media_count']})")
        
        md_lines.append(f"### 1. Target Comments ({len(rep['comments'])})")
        for c in rep["comments"]:
            md_lines.append(f"- **[Comment #{c['id']} @ P[{c['p_idx']}]]**: {c['comment']}")
            md_lines.append(f"  - *Exact Paragraph*: `{c['anchor_paragraph']}`")

        md_lines.append(f"\n### 2. Placeholders & Chinese Notes ({len(rep['flags'])})")
        for fl in rep["flags"]:
            md_lines.append(f"- **P[{fl['p_idx']}]** Flags `{fl['matches']}`: `{fl['exact_paragraph']}`")

        md_lines.append(f"\n### 3. Figures, Tables & Drawings Index ({len(rep['captions'])})")
        for cap in rep["captions"]:
            img_tag = "[IMG]" if cap["has_image"] else "[CAP]"
            md_lines.append(f"- P[{cap['p_idx']}] {img_tag}: {cap['text']}")
        md_lines.append("\n---\n")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print(f"[OK] Digest written to {args.out} ({len(md_lines)} lines).")

def apply_revisions_xml(input_docx, output_docx, edits, author="Chunli Wang"):
    temp_dir = f"_tmp_{os.path.basename(input_docx)}"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    with zipfile.ZipFile(input_docx, 'r') as z:
        z.extractall(temp_dir)

    doc_path = os.path.join(temp_dir, 'word', 'document.xml')
    tree = etree.parse(doc_path)
    root = tree.getroot()
    date_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    rev_id = 9000
    success_count = 0

    for edit in edits:
        old_text = edit["find"].strip()
        new_text = edit["replace"].strip()
        target_p_idx = edit.get("p_idx", None)
        matched = False

        paragraphs = root.xpath('//w:body/w:p', namespaces=NS)
        search_Auto = [paragraphs[target_p_idx]] if (target_p_idx is not None and target_p_idx < len(paragraphs)) else paragraphs

        for p in search_Auto:
            p_full_text = get_p_text(p)
            if old_text in p_full_text:
                first_rPr = p.find('.//w:r/w:rPr', namespaces=NS)
                prefix, remainder = p_full_text.split(old_text, 1)

                for child in list(p):
                    if child.tag in (f"{{{NS_W}}}r", f"{{{NS_W}}}ins", f"{{{NS_W}}}del"):
                        if len(child.xpath('.//w:drawing', namespaces=NS)) == 0:
                            p.remove(child)

                def make_run(text, is_del=False):
                    r = etree.Element(f"{{{NS_W}}}r")
                    if first_rPr is not None:
                        r.append(etree.fromstring(etree.tostring(first_rPr)))
                    t_tag = "delText" if is_del else "t"
                    t = etree.SubElement(r, f"{{{NS_W}}}{t_tag}")
                    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    t.text = text
                    return r

                if prefix:
                    p.append(make_run(prefix))
                if old_text:
                    del_el = etree.SubElement(p, f"{{{NS_W}}}del", {
                        f"{{{NS_W}}}id": str(rev_id), f"{{{NS_W}}}author": author, f"{{{NS_W}}}date": date_str
                    })
                    rev_id += 1
                    del_el.append(make_run(old_text, is_del=True))
                if new_text:
                    ins_el = etree.SubElement(p, f"{{{NS_W}}}ins", {
                        f"{{{NS_W}}}id": str(rev_id), f"{{{NS_W}}}author": author, f"{{{NS_W}}}date": date_str
                    })
                    rev_id += 1
                    ins_el.append(make_run(new_text, is_del=False))
                if remainder:
                    p.append(make_run(remainder))

                matched = True
                success_count += 1
                break
        if not matched:
            print(f"[MISS] File={input_docx} | Find={old_text[:50]}...")

    tree.write(doc_path, xml_declaration=True, encoding='UTF-8', standalone="yes")
    with zipfile.ZipFile(output_docx, 'w', zipfile.ZIP_DEFLATED) as zf:
        for foldername, _, filenames in os.walk(temp_dir):
            for filename in filenames:
                filepath = os.path.join(foldername, filename)
                zf.write(filepath, os.path.relpath(filepath, temp_dir))
    shutil.rmtree(temp_dir)
    print(f"[DONE] {output_docx}: {success_count}/{len(edits)} edits applied.")

def cmd_apply(args):
    with open(args.plan, "r", encoding="utf-8") as f:
        plan = json.load(f)
    for task in plan:
        apply_revisions_xml(task["input_file"], task["output_file"], task["edits"], task.get("author", "Chunli Wang"))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Co_Reviewer CLI for OpenCode Workers")
    sub = parser.add_subparsers(dest="cmd")

    p_ins = sub.add_parser("inspect")
    p_ins.add_argument("--rev", required=True)
    p_ins.add_argument("--man", required=True)
    p_ins.add_argument("--si", required=True)
    p_ins.add_argument("--author", default="")
    p_ins.add_argument("--out", default="workspace_digest.md")

    p_app = sub.add_parser("apply")
    p_app.add_argument("--plan", required=True)

    args = parser.parse_args()
    if args.cmd == "inspect":
        cmd_inspect(args)
    elif args.cmd == "apply":
        cmd_apply(args)
