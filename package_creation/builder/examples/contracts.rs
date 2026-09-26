fn main() {
    let mode = std::env::args().nth(1).unwrap_or_else(|| "schemas".into());
    let value = if mode == "capabilities" {
        ts2_package_builder::contracts::capabilities()
    } else {
        ts2_package_builder::contracts::schemas()
    };
    println!("{}", serde_json::to_string_pretty(&value).unwrap());
}
