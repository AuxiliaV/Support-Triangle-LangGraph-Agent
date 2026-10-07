from langgraph.graph import START , END , StateGraph
from langgraph.checkpoint.memory import InMemorySaver
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from typing import TypedDict , Dict , Any, Literal
from pydantic import BaseModel , Field

from tools import (REFUND_APPROVAL_THRESHOLD , check_refund_eligibility , create_escalation_ticket , 
                   get_order_amount , get_order_status , process_refund)

import os
import dotenv
dotenv.load_dotenv(".env")
openai_api_key = os.getenv("OPENAI_API_KEY")

def get_llm():
    return ChatOpenAI(model = "gpt-4o-mini" ,  temperature = 0)

class State(TypedDict):
    customer_message: str
    customer_id: str
    category: str
    order_id: str
    eligible: bool
    need_approval: bool
    approved: bool|None
    resolution: str

class Classification(BaseModel):
    category: Literal["order_inquiry" , "refund_request" , "complaint" , "policy_question" , "others"]
    order_id: str | None = Field(default=None , 
                                 description = "The order ID mentioned in the message. eg., '1001', or null if none was mentioned")
    customer_id: str | None = Field(default=None , 
                                    description = "The customer ID mentioned in the message. eg., 'C001', or null if none was mentioned")


PROMPT = ChatPromptTemplate.from_messages([
    ("system" , "Classify the customer message into exactly one category:\n"
    "- order_inquiry: asking about an order status or delivery\n"
    "- refund_request: asking for a refund or return\n"
    "- complaint: unhappy, frustrated, or reporting a problem\n"
    "- policy_question: asking about policy or rules, not a specific order\n"
    "- others: anything else\n"
    "Also get the order ID if one is mentioned (just the number, e.g. '1004') and also customer ID (e.g: C002)"),
    ("human" , "{message}"),
])

def classify_node(state:State) -> dict:
    chain = PROMPT | get_llm().with_structured_output(Classification)
    result: Classification = chain.invoke({"message" : state["customer_message"]})
    return {"category":result.category , "order_id":result.order_id , "customer_id":result.customer_id}

def route_after_classify(state:State) -> Literal["order_lookup" , "refund_check" , "escalate"]:
    if state["category"] == "order_inquiry":
        return "order_lookup"
    if state["category"] == "refund_request":
        return "refund_check"
    return "escalate"

def order_lookup_node(state:State) -> dict:
    if not state.get("order_id"):
        return{"resolution": get_order_status(state["order_id"])}
    else:
        return {"resolution": get_order_status(state["order_id"])}

def refund_check_node(state:State) -> dict:
    if not state.get("order_id"):
        return {"resolution": "Please share the order ID" , "eligible": False , "need_approval": False}

    #eligibility_text = refund_tool(state["order_id"])

    eligibility_text = check_refund_eligibility(state.get("order_id"))
    eligible = "eligible for a refund" in eligibility_text
    need_approval = "Requires human approval" in eligibility_text

    if not eligible:
        return {"eligible" : False , "need_approval" : False , "resolution" : eligibility_text}
    
    return {"eligible":True , "need_approval":need_approval , "resolution" : eligibility_text}

def route_after_refund_check(state:State) -> Literal["human_approval" , "process_refund" , "end"]:
    if not state["eligible"]:
        return "end"
    if state["need_approval"]:
        return "human_approval"
    return "process_refund"


def human_approval_node(state:State) -> dict:
    amount = get_order_amount(state["order_id"])

    decision = input(f"\n\nApprove the Refund for order:{state['order_id']}, amount {amount}?\n\n")

    if decision.lower()=="yes":
        return {"approved":True}
    return {"approved":False}


def route_after_approval(state:State) -> Literal["process_refund"]:
    return "process_refund"

def process_refund_node(state:State) ->dict:
    if state.get("approved") is False:
        return {"resolution": f"The refund for order {state['order_id']} was reviewed and not approved. A team member will follow-up with more details."}
    amount = get_order_amount(state["order_id"])
    return {"resolution": process_refund(state["order_id"] , amount)}

def escalate_node(state:State) ->dict:
    result = create_escalation_ticket(
        customer_id = state["customer_id"],
        order_id = state.get("order_id"),
        category = state["category"],
        summary = state["customer_message"][:200],
    )
    return {"resolution": f"Concern is raised, our team member will follow-up with you shortly" 
           f"({result})"}

def build_graph():
    graph = StateGraph(State)
    graph.add_node("classify",classify_node)
    graph.add_node("order_lookup",order_lookup_node)
    graph.add_node("refund_check",refund_check_node)
    graph.add_node("human_approval",human_approval_node)
    graph.add_node("process_refund",process_refund_node)
    graph.add_node("escalate",escalate_node)

    graph.add_edge(START,"classify")
    graph.add_conditional_edges("classify",route_after_classify,{"order_lookup":"order_lookup",
                                                            "refund_check":"refund_check",
                                                            "escalate":"escalate",})
    graph.add_conditional_edges("refund_check",route_after_refund_check,{"human_approval":"human_approval",
                                                                    "process_refund":"process_refund",
                                                                    "end":END,})
    graph.add_conditional_edges("human_approval",route_after_approval,{"process_refund":"process_refund",
                                                                  "end":END})

    graph.add_edge("order_lookup",END)
    graph.add_edge("process_refund",END)
    graph.add_edge("escalate",END)

    return graph.compile(checkpointer=InMemorySaver())

def app(inputs):
    apps = build_graph()
    config = {"configurable": {"thread_id": "user_session_123"}}
    outputs = apps.invoke({"customer_message":inputs},config)
    return outputs['resolution']

