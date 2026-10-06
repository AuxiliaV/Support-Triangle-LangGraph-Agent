import graph_flow as g
import gradio 
import time

def loading_wrapper(user_input):
    # Display your customized processing message immediately
    yield "Some requests require human approval. Please wait while processing..."
    
    # Optional: Short delay to ensure the user actually reads the status message
    time.sleep(1.0) 
    
    # Call your actual function from the graph_flow module
    actual_output = g.app(user_input)
    
    # Display the final output
    yield actual_output

demo = gradio.Interface( fn=loading_wrapper, inputs=[gradio.Textbox(placeholder="Please enter your request along with your order_id. e.g: 0001")],
                        outputs=["text"],
                        title = "Acme Support Agent")

demo.launch(share=True)