INSERT INTO sellers VALUES (1,'Aster Market','gold'),(2,'Boreal Goods','silver'),(3,'Cinder House','bronze');
INSERT INTO buyers VALUES (1,'Dune Buyer'),(2,'Ember Buyer'),(3,'Fjord Buyer');
INSERT INTO listings VALUES (101,1,'books',20,true),(102,1,'tools',35,true),(103,2,'games',50,false),(104,3,'home',15,true);
INSERT INTO orders VALUES (201,1,'2026-06-01','completed'),(202,1,'2026-06-03','pending'),(203,2,'2026-06-05','completed'),(204,3,'2026-06-07','cancelled');
INSERT INTO order_lines VALUES (301,201,101,2),(302,202,102,1),(303,203,103,3),(304,204,104,1);
INSERT INTO payouts VALUES (401,1,'2026-06-10',40,'posted'),(402,2,'2026-06-10',150,'held');
INSERT INTO reviews VALUES (501,101,5,'2026-06-11'),(502,103,3,'2026-06-11'),(503,104,4,'2026-06-12');
INSERT INTO disputes VALUES (601,202,'2026-06-04','open');
INSERT INTO external_directory VALUES (701,1,'seller@example.invalid');
