import json
import sys
from config import setting
from . import prompts
from .state import BasicState
from . import schema
from bs4 import BeautifulSoup
import requests
import re
import base64

def google_search_agent(state: BasicState):
    try:
        content = ""
        for i, result in enumerate(state['google'].search(state['question'], num=3), 1):
            content += f"{i}. {result['title']}\n"
            content += f"   {result['snippet']}\n\n"
            response = requests.get(result['link'])

            if response.status_code == 200:
                if "application/pdf" in response.headers.get("Content-Type", ""):
                    continue
                soup = BeautifulSoup(response.text, 'html.parser')
                if soup.find('article'):
                    context = soup.find('article')
                elif soup.find('main'):
                    context = soup.find('main')
                elif soup.find('div', class_='content'):
                    context = soup.find('div', class_='content')
                elif soup.find('div', class_='article'):
                    context = soup.find('div', class_='article')
                elif soup.find('div', id='content'):
                    context = soup.find('div', id='content')
                if context:
                    content += f"{context.get_text(strip=True)}\n"

    except:
        return None

    if setting["debug"]:
        print("# google_search_agent:\n")
        print(content)

    return {
        "search_results": [content]
    }


def aralia_search_agent(state: BasicState):
    # search multi dataset
    datasets = state["at"].search_tool(state["question"])

    extract_prompt = prompts.simple_datasets_extract_template.invoke(
        {
            "question": state["question"],
            "datasets": datasets
        }
    )

    structured_llm = state["ai"].with_structured_output(
        schema.datasets_extract_output
    )

    for _ in range(5):
        try:
            # extract datasets
            response = structured_llm.invoke(extract_prompt).dict()

            filtered_datasets = [
                datasets[item] for item in response['dataset_key']
            ]
            break
        except:
            continue
    else:
        raise RuntimeError("無法找到可能回答問題的資料集，程式終止")

    if setting["debug"]:
        print("# aralia_search_agent:\n")
        print([item["name"] for item in datasets.values()], end="\n\n")
        print([item["name"] for item in filtered_datasets], end="\n\n")

    return {
        "response": filtered_datasets
    }


def analytics_planning_agent(state: BasicState):
    datasets = state["at"].column_metadata_tool(state['response'])

    if not datasets:
        raise RuntimeError("無法跟搜尋到的星球要資料，程式終止")
    
    plot_chart_prompt = prompts.chart_ploting_template.invoke(  # extract column
        {
            "question": state["question"], 
            "datasets": datasets,
            "admin_level": prompts.admin_level
        }
    )

    for _ in range(5):
        try:
            response = state["ai"].invoke(plot_chart_prompt)

            if setting['debug'] == 3:
                print(response.content, end="\n\n")

            response_json = json.loads(list(re.finditer(
                r'```json(.*?)```', response.content, re.DOTALL))[-1].group(1))
            
            filtered_datasets = [
                {
                    **{k: v for k, v in datasets[chart['id']].items() if k != 'columns'},
                    "x": [
                        {
                            **datasets[chart['id']]['columns'][x['columnID']],
                            "format": x["format"]
                            if x["type"] not in ["date", "datetime", "space"]
                            else x["format"] if (
                                (x["type"] in ["date", "datetime"] and (x["format"] in prompts.format["date"] or (_ := None))) or
                                (x["type"] == "space" and (x["format"] in prompts.format["space"] or (_ := None)))
                            )
                            else x["format"]
                        }
                        for x in chart["x"]
                    ],
                    "y": [
                        {
                            **datasets[chart['id']]['columns'][y['columnID']],
                            'calculation': y['calculation']
                        }
                        for y in chart['y'] 
                        if y['type'] in ["integer", "float"] and (
                            y['calculation'] in prompts.format['calculation'] or (_ := None)  # 檢查計算方法
                        )
                    ],
                    "filter": [
                        {
                            **datasets[chart['id']]['columns'][f['columnID']],
                            "format": f["format"]
                            if f["type"] not in ["date", "datetime", "space"]
                            else f["format"] if (
                                (f["type"] in ["date", "datetime"] and (f["format"] in prompts.format["date"] or (_ := None))) or
                                (f["type"] == "space" and (f["format"] in prompts.format["space"] or (_ := None)))
                            )
                            else f["format"]
                        }
                        for f in chart["filter"]
                    ]
                }
                for chart in response_json["charts"]
            ]
            break
        except Exception as e:
            if setting['debug'] == 3:
                print(f"發生錯誤: {e}")
            continue
    else:
        raise RuntimeError("AI模型無法產出準確的api調用")

    if setting["debug"]:
        print("# analytics_planning_agent:\n")
        print(json.dumps(filtered_datasets, ensure_ascii=False, indent=2), end="\n\n")

    return {
        "response": filtered_datasets
    }


def filter_decision_agent(state: BasicState):
    state["at"].filter_option_tool(state['response'])

    prompt = prompts.query_generate_template.invoke(
        {
            "question": state['question'],
            "response": state['response'],
        }
    )

    structured_llm = state["ai"].with_structured_output(
        schema.query_list
    )

    for _ in range(5):
        try:
            response = structured_llm.invoke(prompt).dict()['querys']
            for chart in response:
                for x in chart["x"]:
                    if x["type"] not in {"date", "datetime", "space"}:
                        x.pop("format")
                for filter in chart["filter"]:
                    if filter["type"] not in {"date", "datetime", "space"}:
                        filter.pop("format")
                chart["filter"] = [chart["filter"]]
            break
        except:
            continue
    else:
        raise RuntimeError("AI模型無法選擇準確的filter value")

    if setting['debug'] == 3:
        print("# filter_decision_agent\n")
        print(json.dumps(response, ensure_ascii=False, indent=2), end="\n\n")

    return {
        "response": response
    }


def analytics_execution_agent(state: BasicState):
    if setting["debug"]:
        print("# analytics_execution_agent:\n")

    # 準備admin_level 資訊
    filtered_datasets = [
        {"id": item["id"], "name": item["name"], "description": item["description"]} for item in state['response']
    ]

    prompt = prompts.space_info_template.invoke(
        {"datasets": filtered_datasets}
    )

    structured_llm = state["ai"].with_structured_output(
        schema.dataset_space_info_list
    )

    for _ in range(5):
        try:
            response = structured_llm.invoke(prompt).dict()['datasets']
            metadata_dict = {item["id"]: item for item in response}

            for data in state['response']:
                data["admin_level"] = prompts.admin_level[
                    metadata_dict[data["id"]]['region']][metadata_dict[data["id"]]['language']
                                                         ]
            break
        except:
            continue
    else:
        raise RuntimeError("更新地區失敗")

    prompt = prompts.query_generate_template.invoke(
        {
            "question": state['question'],
            "response": state['response'],
        }
    )

    structured_llm = state["ai"].with_structured_output(
        schema.query_list
    )

    # 第一次填寫format, calculation
    for _ in range(5):
        try:
            response = structured_llm.invoke(prompt).dict()['querys']
            for chart in response:
                for filter in chart["filter"]:
                    if filter["type"] not in {"date", "datetime", "space"}:
                        filter.pop("format")
                    elif filter["type"] == "space":
                        if filter["format"] not in {
                            "admin_level_2",
                            "admin_level_4",
                            "admin_level_7",
                            "admin_level_8",
                            "admin_level_9",
                            "admin_level_10"
                        }:
                            raise
                    filter.pop("type")
                    filter.pop("operator")
                    filter.pop("value")

            # 準備filter-options
            state["at"].filter_option_tool(response)

            if setting['debug'] == 3:
                print("# get filter")
                print(json.dumps(response, ensure_ascii=False, indent=2), end="\n\n")

            break
        except:
            continue
    else:
        raise RuntimeError("無法成功調用aralia的api取得資料，這意味著參數填寫錯誤。")

    # 第二次填寫filter
    prompt = prompts.query_generate_template_2.invoke(
        {
            "question": state['question'],
            "response": response,
        }
    )

    for _ in range(5):
        try:
            response = structured_llm.invoke(prompt).dict()['querys']

            for chart in response:
                for x in chart["x"]:
                    if x["type"] not in {"date", "datetime", "space"}:
                        x.pop("format")
                for filter in chart["filter"]:
                    if filter["type"] not in {"date", "datetime", "space"}:
                        filter.pop("format")
                    elif filter["type"] == "space":
                        if filter["format"] not in {
                            "admin_level_2",
                            "admin_level_4",
                            "admin_level_7",
                            "admin_level_8",
                            "admin_level_9",
                            "admin_level_10"
                        }:
                            raise
                chart["filter"] = [chart["filter"]]

            if setting['debug'] == 3:
                print("# explore api request")
                print(json.dumps(response, ensure_ascii=False, indent=2), end="\n\n")

            break
        except:
            continue
    else:
        raise RuntimeError("無法成功調用aralia的api取得資料，這意味著參數填寫錯誤。")

    state["at"].explore_tool(response)

    final_response = {
        "json_data": response[0]['json_data'],
        "image": response[0]['image']
    }

    response[0].pop("image")

    return {
        "search_results": [response],
        "final_response": final_response
    }


def interpretation_agent(state: BasicState):
    messages = [
        {
            "role": "system",
            "content": "You are a Senior Data Analyst with expertise in analyzing statistical data. You excel at uncovering insights from the data and identifying relationships between different datasets.",
        },
        {
            "role": "user",
            "content": f"""
                問題: ***{state['question']}***
                資訊: {state['search_results']}

                我已經根據用戶的問題找來了相關的資訊，
                請詳細分析以上資訊後詳細回答問題，並給出結論。
                請特別注意"json_data"或者"image"才是實際取得的資料，請幫我仔細分析資料或者圖片內容。
                
                輸出格式：
                - 語言請用"{state['language']}"
            """,
        },
    ]

    response = state["ai"].invoke(messages)

    if setting["debug"]:
        print("# interpretation_agent:\n")
        print(response.content)

    result = state['final_response']
    result['text_response'] = response.content

    return {
        "final_response": result
    }
