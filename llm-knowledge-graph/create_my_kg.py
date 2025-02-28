import os

from dotenv import load_dotenv
from langchain.text_splitter import CharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_community.graphs.graph_document import Node, Relationship
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_neo4j import Neo4jGraph
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

load_dotenv()

DOCS_PATH = "llm-knowledge-graph/data/course/pdfs"
DATA_PATH = "llm-knowledge-graph/data/course/csvs"


llm = ChatOpenAI(
    openai_api_key=os.getenv('OPENAI_API_KEY'), 
    model_name="gpt-3.5-turbo"
)

embedding_provider = OpenAIEmbeddings(
    openai_api_key=os.getenv('OPENAI_API_KEY'),
    model="text-embedding-ada-002"
    )

graph = Neo4jGraph(
    url=os.getenv('NEO4J_URI'),
    username=os.getenv('NEO4J_USERNAME'),
    password=os.getenv('NEO4J_PASSWORD')
)

doc_transformer = LLMGraphTransformer(
    llm=llm,
    allowed_nodes = [
    # Nodes from the SQL schema subset
    "Claim",
    "Claim_Amount",
    "Loss_Payment",
    "Loss_Reserve",
    "Expense_Payment",
    "Expense_Reserve",
    "Claim_Coverage",
    "Policy_Coverage_Detail",
    "Policy",
    "Policy_Amount",
    "Agreement_Party_Role",
    "Premium",
    "Catastrophe",
    
    # Nodes from the ontology
    "in:Claim",
    "in:PolicyCoverageDetail",
    "in:Policy",
    "in:Catastrophe",
    "in:ExpensePayment",
    "in:ExpenseReserve",
    "in:LossPayment",
    "in:LossReserve"
],
allowed_relationships = [
    "in:against",          # Links a Claim to a PolicyCoverageDetail
    "in:hasCatastrophe",   # Links a Claim to a Catastrophe
    "in:hasExpensePayment",# Links a Claim to an ExpensePayment
    "in:hasExpenseReserve",# Links a Claim to an ExpenseReserve
    "in:hasLossPayment",   # Links a Claim to a LossPayment
    "in:hasLossReserve",   # Links a Claim to a LossReserve
    "in:hasPolicy"         # Links a PolicyCoverageDetail to a Policy
]
    )

# Load and split the documents

# Load all CSV files from the directory
loader = DirectoryLoader(DATA_PATH, glob="**/*.csv", loader_cls=CSVLoader)

text_splitter = CharacterTextSplitter(
    separator="\n\n",
    chunk_size=1500,
    chunk_overlap=200,
)

docs = loader.load()
chunks = text_splitter.split_documents(docs)

for chunk in chunks:

    filename = os.path.basename(chunk.metadata["source"])
    chunk_id = f"{filename}.{chunk.metadata['page']}"
    print("Processing -", chunk_id)

    # Embed the chunk
    chunk_embedding = embedding_provider.embed_query(chunk.page_content)

    # Add the Document and Chunk nodes to the graph
    properties = {
        "filename": filename,
        "chunk_id": chunk_id,
        "text": chunk.page_content,
        "embedding": chunk_embedding
    }
    
    graph.query("""
        MERGE (d:Document {id: $filename})
        MERGE (c:Chunk {id: $chunk_id})
        SET c.text = $text
        MERGE (d)<-[:PART_OF]-(c)
        WITH c
        CALL db.create.setNodeVectorProperty(c, 'textEmbedding', $embedding)
        """, 
        properties
    )

    # Generate the entities and relationships from the chunk
    graph_docs = doc_transformer.convert_to_graph_documents([chunk])

    # Map the entities in the graph documents to the chunk node
    for graph_doc in graph_docs:
        chunk_node = Node(
            id=chunk_id,
            type="Chunk"
        )

        for node in graph_doc.nodes:

            graph_doc.relationships.append(
                Relationship(
                    source=chunk_node,
                    target=node, 
                    type="HAS_ENTITY"
                    )
                )

    # add the graph documents to the graph
    graph.add_graph_documents(graph_docs)

# Create the vector index
graph.query("""
    CREATE VECTOR INDEX `chunkVector`
    IF NOT EXISTS
    FOR (c: Chunk) ON (c.textEmbedding)
    OPTIONS {indexConfig: {
    `vector.dimensions`: 1536,
    `vector.similarity_function`: 'cosine'
    }};""")
