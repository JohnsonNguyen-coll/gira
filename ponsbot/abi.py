def function(name, inputs=(), outputs=(), mutability="view"):
    return {"type": "function", "name": name, "stateMutability": mutability,
            "inputs": [{"name": n, "type": t} for n, t in inputs],
            "outputs": [{"name": n, "type": t} for n, t in outputs]}


def event(name, fields):
    return {"type": "event", "name": name, "anonymous": False,
            "inputs": [{"name": n, "type": t, "indexed": i} for n, t, i in fields]}


LAUNCH = event("TokenLaunched", [
    ("token", "address", True), ("curve", "address", True),
    ("deployer", "address", True), ("pairToken", "address", False),
    ("launchConfigId", "uint256", False), ("graduationThreshold", "uint256", False)])
BUY = event("CurveBuy", [
    ("buyer", "address", True), ("recipient", "address", True),
    ("quoteIn", "uint256", False), ("tokensOut", "uint256", False),
    ("fee", "uint256", False), ("tax", "uint256", False)])
FACTORY_ABI = [LAUNCH]
TOKEN_ABI = [function("symbol", outputs=[("", "string")]),
             function("totalSupply", outputs=[("", "uint256")])]
CURVE_ABI = [BUY,
    function("getReserves", outputs=[("quoteReserve", "uint256"), ("tokenReserve", "uint256")]),
    *[function(n, outputs=[("", "uint256")]) for n in
      ("sellableTokens", "feeBps", "creatorTaxBps")],
    function("currentSnipeTaxBps", [("recipient", "address")], [("", "uint256")]),
    function("isNativeQuote", outputs=[("", "bool")]),
    function("buy", [("quoteIn", "uint256"), ("minTokensOut", "uint256"),
                     ("recipient", "address")], [("tokensOut", "uint256")], "payable")]
