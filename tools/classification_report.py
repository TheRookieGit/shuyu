"""Export a local classification inventory and an honest coverage report."""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Library
from book_classification import display_label, TAGS


def safe_cell(value):
    value=str(value) if value is not None else ''
    return "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',default=str(Path(__file__).resolve().parents[1]/'.data'))
    args=parser.parse_args();lib=Library(Path(args.data_dir));directory=lib.classifier.directory
    summary=lib.classifier.summary();levels={};relations={};errors={};reviewed=0;total=0
    bg_pairings={'1v1':0,'np':0,'待确认':0};tag_counts={tag:0 for tag in TAGS}
    with lib.db() as db,(directory/'分类清单.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['ID','书名','作者','分类组合','第一分类','第二分类','BG子类','tag','字数','分类状态','人工核验','读取限制','原始位置'])
        for row in db.execute("SELECT b.id,b.title,b.author,b.rel,c.level,c.relationship,c.pairing,c.tags,c.status,c.reviewed,m.word_count,m.error FROM books b LEFT JOIN book_classification c ON c.book_id=b.id LEFT JOIN book_text_metrics m ON m.book_id=b.id AND m.version_id='' WHERE b.root=? AND b.missing=0 AND b.kind<>'archive' ORDER BY b.title",(lib.root,)):
            value=dict(row);value['tags']=json.loads(value['tags'] or '[]');total+=1;reviewed+=bool(value['reviewed'])
            level=value['level'] or '待确认';relation=value['relationship'] or '待确认';levels[level]=levels.get(level,0)+1;relations[relation]=relations.get(relation,0)+1
            if relation=='BG':bg_pairings[value['pairing'] or '待确认']+=1
            for tag in value['tags']:tag_counts[tag]+=1
            if value['error']:errors[value['error']]=errors.get(value['error'],0)+1
            writer.writerow(map(safe_cell,[value['id'],value['title'],value['author'],display_label(value),value['level'],value['relationship'],value['pairing'],' '.join(value['tags']),value['word_count'],value['status'] or 'pending','是' if value['reviewed'] else '否',value['error'],value['rel']]))
    report={'total':total,'word_counted':summary['counted'],'not_counted':total-summary['counted'],'statuses':summary['statuses'],
            'levels':levels,'relationships':relations,'bg_pairings':bg_pairings,'tags':tag_counts,'manually_reviewed_records':reviewed,'read_limits':errors,
            'claude_used_for_initial_pass':False,'count_basis':summary['count_basis']}
    (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 首次小说整理报告','',f'- 书库记录：{total:,} 条。',f'- 已取得文件字数：{summary["counted"]:,} 条；暂不能可靠统计：{total-summary["counted"]:,} 条。',
        f'- 初筛分级与关系字段已填齐：{summary["statuses"].get("classified",0):,} 条。',f'- 尚有字段待确认或标记冲突：{total-summary["statuses"].get("classified",0):,} 条。',f'- 人工核验并保护的记录：{reviewed:,} 条。','',
        '本轮没有调用 Claude API 或 Claude 订阅。大库采用本地文件名、简介及文字特征初筛，另对部分有歧义的作品核验出版目录；初筛不等于逐本人工通读，未人工核验的结果仍需复核。','',
        '清水不会由“未检出露骨词语”自动推出。无法确定的分级、关系或 BG 子类保留待确认；题材 tag 可多选，也可为空。','',
        '## 第一分类','',*['- '+key+'：'+f'{count:,}' for key,count in levels.items()],'','## 第二分类','',*['- '+key+'：'+f'{count:,}' for key,count in relations.items()],
        '','## BG 子类','',*['- '+key+'：'+f'{count:,}' for key,count in bg_pairings.items()],
        '','## 题材 tag（可多选，不直接求和）','',*['- '+key+'：'+f'{count:,}' for key,count in tag_counts.items()],
        '','## 字数口径','',summary['count_basis']+' 清理版独立统计，书卡展示原始文件字数；详情切换版本会显示对应版本的字数。','',
        '## 读取限制','',*['- '+key+'：'+f'{count:,}' for key,count in sorted(errors.items(),key=lambda item:-item[1])],
        '','[完整分类清单](分类清单.csv)包含待确认字段、字数及读取限制。文件留在本机 .data 中，不提交到 Git。']
    review_file=directory/'reviewed-books.json'
    if review_file.is_file():
        lines+=['','## 核验示例与来源','']
        for item in json.loads(review_file.read_text(encoding='utf-8')):
            lines.append(f'- [{item["title"]}]({item["source_url"]})：{item["reason"]}（关联 {item["applied"]} 条记录）')
    (directory/'整理报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))


if __name__=='__main__':main()
