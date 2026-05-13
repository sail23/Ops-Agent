"""Neo4j 连接测试"""
import sys

try:
    from neo4j import GraphDatabase
except ImportError:
    print("neo4j 驱动未安装，正在安装...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "neo4j", "-q"])
    from neo4j import GraphDatabase

NEO4J_URI = "bolt://127.0.0.1:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "cf178025"

print(f"尝试连接: {NEO4J_URI}")
print(f"用户名: {NEO4J_USER}")

try:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    # 简单测试查询
    with driver.session() as session:
        result = session.run("RETURN '连接成功!' AS msg")
        record = result.single()
        print(f"[OK] {record['msg']}")
    
    driver.close()
    print("\n[OK] Neo4j 连接测试通过!")
    
except Exception as e:
    print(f"\n[FAIL] 连接失败: {e}")
    sys.exit(1)