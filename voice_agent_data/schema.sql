PRAGMA foreign_keys=ON;
CREATE TABLE menu_items(item_id INTEGER PRIMARY KEY,name TEXT,category TEXT,price_usd REAL,description TEXT,allergen_info_verified INTEGER,available INTEGER);
CREATE TABLE ingredients(ingredient_id INTEGER PRIMARY KEY,name TEXT);
CREATE TABLE menu_item_ingredients(item_id INTEGER REFERENCES menu_items,ingredient_id INTEGER REFERENCES ingredients);
CREATE TABLE menu_item_allergens(item_id INTEGER REFERENCES menu_items,allergen TEXT,status TEXT CHECK(status IN('contains','may_contain','free')));
CREATE TABLE opening_hours(day_of_week INTEGER,open TEXT,close TEXT,kitchen_close TEXT);
CREATE TABLE tables(table_id TEXT PRIMARY KEY,seats INTEGER,section TEXT);
CREATE TABLE reservations(reservation_id TEXT PRIMARY KEY,table_id TEXT REFERENCES tables,date TEXT,start_time TEXT,end_time TEXT,party_size INTEGER,guest_name TEXT,phone TEXT,status TEXT);
