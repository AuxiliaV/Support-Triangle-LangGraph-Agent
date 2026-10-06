import os
import uuid
import pandas as pd
import datetime
from datetime import datetime as dt
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

path = os.getcwd()
path = path.replace("\\","/")
print(path)

REFUND_APPROVAL_THRESHOLD = 100.00
RETURN_WINDOW_DAYS = 30

ORDERS_CSV = path+"/"+"data/orders.csv"
REFUNDS_CSV = path+"/"+"data/refunds.csv"
TICKETS_CSV = path+"/"+"data/tickets.csv"

ID_DTYPES = {"order_id":str , "customer_id":str , "refund_id":str , "ticket_id":str}

def read_csv(path , columns:list) -> pd.DataFrame:
    if path:
        df = pd.read_csv(path , dtype = ID_DTYPES , keep_default_na = False)
    else:
        raise FileNotFoundError(f"{path} not found")

    return df if not df.empty else pd.DataFrame(columns = columns)


def read_orders() -> pd.DataFrame:
    return read_csv(ORDERS_CSV , ["order_id" , "customer_id" , "item" , "amount" ,
                                 "status" , "order_date" , "delivered_date"])


def read_refunds() -> pd.DataFrame:
    return read_csv(REFUNDS_CSV , ["refund_id" , "order_id" , "amount" , "status" ,
                                   "requested_date"])


def read_tickets() -> pd.DataFrame:
    return read_csv(TICKETS_CSV , ["ticket_id" , "customer_id" , "order_id" , "category",
                                  "summary" , "status" , "created_date"])


def append_row(path , row:dict) -> None:
    df = pd.read_csv(path , dtype = ID_DTYPES , keep_default_na = False) if path else pd.DataFrame()
    df = pd.concat([df , pd.DataFrame([row])] , ignore_index = True)
    df.to_csv(path , index = False)


class OrderLookupInput(BaseModel):
    order_id: str = Field(description = "Order ID example: '1001'")


def get_order_status(order_id: str) -> str:
    orders = read_orders() # Loads the orders dataframe
    match = orders[orders["order_id"] == order_id]

    if match.empty:
        return f"No order found with ID {order_id}, Please double check the order number"
    row = match.iloc[0]
    parts = [f"Order {row['order_id']}: {row['item']}, ${float(row['amount']):.2f},"
            f" status: {row['status']}, ordered: {row['order_date']}"]
    if row["delivered_date"]:
        parts.append(f" delivered: {row['delivered_date']}")
        
    return ". ".join(parts) + "."


order_lookup_tool = StructuredTool.from_function(
    func = get_order_status,
    name = "get_order_status",
    description = "Look up an order's item, amount, status, and dates by order ID.",
    args_schema = OrderLookupInput
    )


def get_order_amount(order_id:str) -> float | None:
    orders = read_orders()
    match = orders[orders["order_id"]==order_id]
    return float(match.iloc[0]["amount"]) if not match.empty else None


class RefundEligibilityInput(BaseModel):
    order_id:str = Field(description = "Order Id to check for eligibility")


def check_refund_eligibility(order_id:str) -> str:
    orders = read_orders()
    order_match = orders[orders["order_id"]==order_id]
    if order_match.empty:
        return f"No order found with ID {order_id}."
    order = order_match.iloc[0]

    refunds = read_refunds()
    existing = refunds[refunds["order_id"]==order_id]
    if not existing.empty:
        r = existing.iloc[0]
        return (f"Order {order_id} was already refunded (${float(r['amount']):.2f},"
               f" status: {r['status']}).")

    if order["status"]!="delivered":
        return (f"Order {order_id} has status '{order['status']}', not delivered. "
        "Refunds are only for delivered orders. If it hasn't shipped yet, it can be cancelled instead.")

    delivered = dt.strptime(order["delivered_date"], "%Y-%m-%d")
    days_since = (dt.now() - delivered).days

    if days_since>RETURN_WINDOW_DAYS:
        return (f"Order {order_id} was delivered {days_since} days ago, which is outside the {RETURN_WINDOW_DAYS} days. Not eligible for a standard refund; escalate if you have an exceptional circumstance.")

    amount = float(order['amount'])
    needs_approval = amount > REFUND_APPROVAL_THRESHOLD
    approval_note = (f"This is above the ${REFUND_APPROVAL_THRESHOLD:.0f} auto-approval threshold. Requires human approval before processing." if needs_approval else 
    f"This is at or under the ${REFUND_APPROVAL_THRESHOLD:.0f} threshold and can be auto-approved.")

    return (f" Order {order_id} (${amount:.2f}) is eligible for a refund."
            f" Delivered {days_since} days ago, within the {RETURN_WINDOW_DAYS} days window"
            f" {approval_note}")

refund_eligibility_tool = StructuredTool.from_function(
    func = check_refund_eligibility,
    name = "check_refund_eligibility",
    description = "Check whether an order is eligible for a refund: not already refunded, delivered, and within the return widow."
    "ALWAYS call this before processing or promising a refund.",
    args_schema = RefundEligibilityInput,
)


class ProcessRefundInput(BaseModel):
    order_id:str = Field(description = "The order ID to refund")
    amount:float = Field(description = "The refund amount in dollars")


def process_refund(order_id:str, amount:float) -> str:
    refund_id = f"R{uuid.uuid4().hex[:6].upper()}"
    append_row(REFUNDS_CSV , {
        "refund_id":refund_id , "order_id":order_id , "amount":amount,
        "status":"completed" , "requested_date":dt.now().strftime("%Y-%m-%d"),
    })
    return f"Refund {refund_id} for ${amount:.2f} on order {order_id} has been processed."


process_refund_tool = StructuredTool.from_function(
    func = process_refund,
    name = "process_refund",
    description = "Process a refund for an order. Only call this after check_refund_eligibility"
    "confirms the order qualifies, and after human approval if required.",
    args_schema = ProcessRefundInput,
)


class EscalationInput(BaseModel):
    customer_id:str = Field(description = "The customer's ID eg: 'C001'")
    order_id:str | None = Field(default = None , description = "Related order ID if any")
    category:str = Field(description = "One of: complaint, shipping_delay, product_issue, others")
    summary:str = Field(description = "A one or two sentence summary of the issue for the human agent")


def create_escalation_ticket(customer_id:str, category:str, summary:str, order_id:str|None) -> str:
    tickets_before = read_tickets()
    prior = int((tickets_before['customer_id']==customer_id).sum())

    ticket_id = f"T{uuid.uuid4().hex[:6].upper()}"
    append_row(TICKETS_CSV, {
        "ticket_id":ticket_id , "customer_id":customer_id , "order_id":order_id or "",
        "category":category , "summary":summary , "status":"open",
        "created_date":dt.now().strftime("%Y-%m-%d"),
    })
    note = f"This customer has a prior ticket(s) on record" if prior else ""
    return f"Escalation ticket {ticket_id} created for customer {customer_id}, category: {category}. {note}"


escalation_tool = StructuredTool.from_function(
    func = create_escalation_ticket,
    name = "create_escalation_ticket",
    description = "Create a ticket for a human agent to handle: complex complaints, anything outside"
    "policy, or any angry/upset customer. Use when you cannot resolve the issue yourself.",
    args_schema = EscalationInput
)


ALL_TOOLS = [order_lookup_tool , refund_eligibility_tool , process_refund_tool , escalation_tool]