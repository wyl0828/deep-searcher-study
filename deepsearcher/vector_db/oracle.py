import array
import json
from typing import List, Optional, Union

import numpy as np

from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.loader.splitter import Chunk
from deepsearcher.utils import log
from deepsearcher.vector_db.base import BaseVectorDB, CollectionInfo, RetrievalResult
from deepsearcher.vector_db.exceptions import (
    CollectionManifestInvalid,
    CollectionManifestWriteFailed,
    UnsafeCollectionReplacement,
)


class OracleDB(BaseVectorDB):
    """OracleDB class is a subclass of DB class."""

    client = None
    default_metric_type = "COSINE"
    supports_collection_manifests = True

    def __init__(
        self,
        user: str,
        password: str,
        dsn: str,
        config_dir: str,
        wallet_location: str,
        wallet_password: str,
        min: int = 1,
        max: int = 10,
        increment: int = 1,
        default_collection: str = "deepsearcher",
    ):
        """
        Initialize the Oracle database connection.

        Args:
            user (str): Oracle database username.
            password (str): Oracle database password.
            dsn (str): Oracle database connection string.
            config_dir (str): Directory containing Oracle configuration files.
            wallet_location (str): Location of the Oracle wallet.
            wallet_password (str): Password for the Oracle wallet.
            min (int, optional): Minimum number of connections in the pool. Defaults to 1.
            max (int, optional): Maximum number of connections in the pool. Defaults to 10.
            increment (int, optional): Increment for adding new connections. Defaults to 1.
            default_collection (str, optional): Default collection name. Defaults to "deepsearcher".
        """
        super().__init__(default_collection)
        self.default_collection = default_collection

        import oracledb

        oracledb.defaults.fetch_lobs = False
        self.DB_TYPE_VECTOR = oracledb.DB_TYPE_VECTOR

        try:
            self.client = oracledb.create_pool(
                user=user,
                password=password,
                dsn=dsn,
                config_dir=config_dir,
                wallet_location=wallet_location,
                wallet_password=wallet_password,
                min=min,
                max=max,
                increment=increment,
            )
            log.color_print("Connected to Oracle database")
            self.check_table()
        except Exception as exc:
            log.critical(log.safe_exception_message("oracle_connect", exc))
            raise

    def numpy_converter_in(self, value):
        """Convert numpy array to array.array"""
        if value.dtype == np.float64:
            dtype = "d"
        elif value.dtype == np.float32:
            dtype = "f"
        else:
            dtype = "b"
        return array.array(dtype, value)

    def input_type_handler(self, cursor, value, arraysize):
        """Set the type handler for the input data"""
        if isinstance(value, np.ndarray):
            return cursor.var(
                self.DB_TYPE_VECTOR,
                arraysize=arraysize,
                inconverter=self.numpy_converter_in,
            )

    def numpy_converter_out(self, value):
        """Convert array.array to numpy array"""
        if value.typecode == "b":
            dtype = np.int8
        elif value.typecode == "f":
            dtype = np.float32
        else:
            dtype = np.float64
        return np.array(value, copy=False, dtype=dtype)

    def output_type_handler(self, cursor, metadata):
        """Set the type handler for the output data"""
        if metadata.type_code is self.DB_TYPE_VECTOR:
            return cursor.var(
                metadata.type_code,
                arraysize=cursor.arraysize,
                outconverter=self.numpy_converter_out,
            )

    def query(self, sql: str, params: dict = None) -> Union[dict, None]:
        """
        Execute a SQL query and return the results.

        Args:
            sql (str): SQL query to execute.
            params (dict, optional): Parameters for the SQL query. Defaults to None.

        Returns:
            Union[dict, None]: Query results as a dictionary or None if no results.

        Raises:
            Exception: If there's an error executing the query.
        """
        with self.client.acquire() as connection:
            connection.inputtypehandler = self.input_type_handler
            connection.outputtypehandler = self.output_type_handler
            with connection.cursor() as cursor:
                try:
                    log.debug("oracle_query_started")
                    # log.debug("def query:"+params)
                    # print("sql:\n",sql)
                    # print("params:\n",params)
                    cursor.execute(sql, params)
                except Exception as exc:
                    log.critical(log.safe_exception_message("oracle_query", exc))
                    raise
                columns = [column[0].lower() for column in cursor.description]
                rows = cursor.fetchall()
                if rows:
                    data = [dict(zip(columns, row)) for row in rows]
                else:
                    data = []
                log.debug(f"oracle_query_completed row_count={len(data)}")
                return data
            # self.client.drop(connection)

    def execute(self, sql: str, data: Union[list, dict] = None):
        """
        Execute a SQL statement without returning results.

        Args:
            sql (str): SQL statement to execute.
            data (Union[list, dict], optional): Data for the SQL statement. Defaults to None.

        Raises:
            Exception: If there's an error executing the statement.
        """
        try:
            with self.client.acquire() as connection:
                connection.inputtypehandler = self.input_type_handler
                connection.outputtypehandler = self.output_type_handler
                with connection.cursor() as cursor:
                    # print("sql:\n",sql)
                    # print("data:\n",data)
                    if data is None:
                        cursor.execute(sql)
                    else:
                        cursor.execute(sql, data)
                    connection.commit()
        except Exception as exc:
            log.critical(log.safe_exception_message("oracle_execute", exc))
            raise

    def has_collection(self, collection: str = "deepsearcher"):
        """
        Check if a collection exists in the database.

        Args:
            collection (str, optional): Collection name to check. Defaults to "deepsearcher".

        Returns:
            bool: True if the collection exists, False otherwise.
        """
        SQL = SQL_TEMPLATES["has_collection"]
        params = {"collection": collection}
        res = self.query(SQL, params)
        if res:
            if res[0]["rowcnt"] > 0:
                return True
            else:
                return False
        else:
            return False

    def collection_exists(self, collection: str) -> bool:
        return self.has_collection(collection)

    def get_collection_manifest(
        self,
        collection: str,
    ) -> CollectionManifest | None:
        rows = self.query(
            SQL_TEMPLATES["get_collection_description"],
            {"collection": collection},
        )
        if not rows:
            return None
        description = rows[0].get("description")
        if not description or not str(description).lstrip().startswith("{"):
            return None
        try:
            return CollectionManifest.from_json(str(description))
        except (TypeError, ValueError) as exc:
            raise CollectionManifestInvalid(
                operation="read_manifest",
                collection=collection,
            ) from exc

    def set_collection_manifest(
        self,
        collection: str,
        manifest: CollectionManifest,
    ) -> None:
        try:
            self.execute(
                SQL_TEMPLATES["update_collection_description"],
                {
                    "collection": collection,
                    "description": manifest.to_json(),
                },
            )
        except Exception as exc:
            raise CollectionManifestWriteFailed(
                operation="write_manifest",
                collection=collection,
            ) from exc

    def check_table(self):
        """
        Check if required tables exist and create them if they don't.

        Raises:
            Exception: If there's an error checking or creating tables.
        """
        SQL = SQL_TEMPLATES["has_table"]
        try:
            res = self.query(SQL)
            if len(res) < 2:
                missing_table = TABLES.keys() - set([i["table_name"] for i in res])
                for table in missing_table:
                    self.create_tables(table)
        except Exception as exc:
            log.critical(log.safe_exception_message("oracle_check_table", exc))
            raise

    def create_tables(self, table_name):
        """
        Create a table in the database.

        Args:
            table_name: Name of the table to create.

        Raises:
            Exception: If there's an error creating the table.
        """
        SQL = TABLES[table_name]
        try:
            self.execute(SQL)
            log.color_print(f"Created table {table_name} in Oracle database")
        except Exception as exc:
            log.critical(log.safe_exception_message("oracle_create_table", exc))
            raise

    def drop_collection(self, collection: str = "deepsearcher"):
        """
        Drop a collection from the database.

        Args:
            collection (str, optional): Collection name to drop. Defaults to "deepsearcher".

        Raises:
            Exception: If there's an error dropping the collection.
        """
        try:
            params = {"collection": collection}
            SQL = SQL_TEMPLATES["drop_collection"]
            self.execute(SQL, params)

            SQL = SQL_TEMPLATES["drop_collection_item"]
            self.execute(SQL, params)
            log.color_print(f"Collection {collection} dropped")
        except Exception as exc:
            log.critical(log.safe_exception_message("oracle_drop_collection", exc))
            raise

    def insertone(self, data):
        """
        Insert a single record into the database.

        Args:
            data: Data to insert.
        """
        SQL = SQL_TEMPLATES["insert"]
        self.execute(SQL, data)
        log.debug("insert done!")

    def searchone(
        self,
        collection: Optional[str],
        vector: Union[np.array, List[float]],
        top_k: int = 5,
    ):
        """
        Search for similar vectors in a collection.

        Args:
            collection (Optional[str]): Collection name to search in.
            vector (Union[np.array, List[float]]): Query vector for similarity search.
            top_k (int, optional): Number of results to return. Defaults to 5.

        Returns:
            list: List of search results.

        Raises:
            Exception: If there's an error during search.
        """
        log.debug("def searchone:" + collection)
        try:
            if isinstance(vector, List):
                vector = np.array(vector)
            embedding_string = "[" + ", ".join(map(str, vector.tolist())) + "]"
            dimension = vector.shape[0]
            dtype = str(vector.dtype).upper()

            SQL = SQL_TEMPLATES["search"].format(dimension=dimension, dtype=dtype)
            max_distance = 0.8
            params = {
                "collection": collection,
                "embedding_string": embedding_string,
                "top_k": top_k,
                "max_distance": max_distance,
            }
            res = self.query(SQL, params)
            if res:
                return res
            else:
                return []
        except Exception as exc:
            log.critical(log.safe_exception_message("oracle_search_one", exc))
            raise

    def init_collection(
        self,
        dim: int,
        collection: Optional[str] = "deepsearcher",
        description: Optional[str] = "",
        force_new_collection: bool = False,
        text_max_length: int = 65_535,
        reference_max_length: int = 2048,
        metric_type: str = "L2",
        *args,
        **kwargs,
    ):
        """
        Initialize a collection in the database.

        Args:
            dim (int): Dimension of the vector embeddings.
            collection (Optional[str], optional): Collection name. Defaults to "deepsearcher".
            description (Optional[str], optional): Collection description. Defaults to "".
            force_new_collection (bool, optional): Whether to force create a new collection if it already exists. Defaults to False.
            text_max_length (int, optional): Maximum length for text field. Defaults to 65_535.
            reference_max_length (int, optional): Maximum length for reference field. Defaults to 2048.
            metric_type (str, optional): Metric type for vector similarity search. Defaults to "L2".
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Raises:
            Exception: If there's an error initializing the collection.
        """
        if not collection:
            collection = self.default_collection
        if description is None:
            description = ""
        try:
            has_collection = self.has_collection(collection)
            if force_new_collection and has_collection:
                raise UnsafeCollectionReplacement(
                    operation="initialize",
                    collection=collection,
                )
            elif has_collection:
                return {
                    "created": False,
                    "collection": collection,
                }
            # insert collection info
            SQL = SQL_TEMPLATES["insert_collection"]
            params = {"collection": collection, "description": description}
            self.execute(SQL, params)
            return {
                "created": True,
                "collection": collection,
            }
        except UnsafeCollectionReplacement:
            raise
        except Exception as exc:
            log.critical(log.safe_exception_message("oracle_init_collection", exc))

    def insert_data(
        self,
        collection: Optional[str],
        chunks: List[Chunk],
        batch_size: int = 256,
        *args,
        **kwargs,
    ):
        """
        Insert data into a collection.

        Args:
            collection (Optional[str]): Collection name. If None, uses default_collection.
            chunks (List[Chunk]): List of Chunk objects to insert.
            batch_size (int, optional): Number of chunks to insert in each batch. Defaults to 256.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Raises:
            Exception: If there's an error inserting data.
        """
        if not collection:
            collection = self.default_collection

        datas = []
        for chunk in chunks:
            _data = {
                "embedding": self.numpy_converter_in(np.array(chunk.embedding)),
                "text": chunk.text,
                "reference": chunk.reference,
                "metadata": json.dumps(chunk.metadata),
                "collection": collection,
            }
            datas.append(_data)

        batch_datas = [datas[i : i + batch_size] for i in range(0, len(datas), batch_size)]
        try:
            for batch_data in batch_datas:
                for _data in batch_data:
                    self.insertone(data=_data)
            log.color_print(f"Successfully insert {len(datas)} data")
        except Exception as exc:
            log.critical(log.safe_exception_message("oracle_insert", exc))
            raise

    def search_data(
        self,
        collection: Optional[str],
        vector: Union[np.array, List[float]],
        top_k: int = 5,
        *args,
        **kwargs,
    ) -> List[RetrievalResult]:
        """
        Search for similar vectors in a collection.

        Args:
            collection (Optional[str]): Collection name. If None, uses default_collection.
            vector (Union[np.array, List[float]]): Query vector for similarity search.
            top_k (int, optional): Number of results to return. Defaults to 5.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            List[RetrievalResult]: List of retrieval results containing similar vectors.

        Raises:
            Exception: If there's an error during search.
        """
        if not collection:
            collection = self.default_collection
        try:
            embedding_profile = kwargs.pop("embedding_profile", None)
            if isinstance(embedding_profile, EmbeddingProfile):
                self.assert_collection_compatible(
                    collection,
                    embedding_profile,
                )
            # print("def search_data:",collection)
            # print("def search_data:",type(vector))
            search_results = self.searchone(collection=collection, vector=vector, top_k=top_k)
            # print("def search_data: search_results",search_results)

            return [
                RetrievalResult.from_metric_value(
                    embedding=b["embedding"],
                    text=b["text"],
                    reference=b["reference"],
                    metadata=json.loads(b["metadata"]),
                    metric_type="COSINE",
                    value=b["distance"],
                    score_kind="distance",
                )
                for b in search_results
            ]
        except Exception as exc:
            log.critical(log.safe_exception_message("oracle_search", exc))
            raise
            # return []

    def list_collections(self, *args, **kwargs) -> List[CollectionInfo]:
        """
        List all collections in the database.

        Args:
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            List[CollectionInfo]: List of collection information objects.
        """
        collection_infos = []
        try:
            SQL = SQL_TEMPLATES["list_collections"]
            log.debug("def list_collections:" + SQL)
            collections = self.query(SQL)
            if collections:
                for collection in collections:
                    raw_description = collection["description"]
                    manifest = None
                    if raw_description and str(raw_description).lstrip().startswith("{"):
                        try:
                            manifest = CollectionManifest.from_json(str(raw_description))
                        except (TypeError, ValueError) as exc:
                            raise CollectionManifestInvalid(
                                operation="list",
                                collection=collection["collection"],
                            ) from exc
                    collection_infos.append(
                        CollectionInfo(
                            collection_name=collection["collection"],
                            description=(
                                manifest.user_description
                                if manifest is not None
                                else raw_description
                            ),
                            manifest=manifest,
                        )
                    )
            return collection_infos
        except Exception as exc:
            log.critical(log.safe_exception_message("oracle_list_collections", exc))
            raise

    def clear_db(self, collection: str = "deepsearcher", *args, **kwargs):
        """
        Clear (drop) a collection from the database.

        Args:
            collection (str, optional): Collection name to drop. Defaults to "deepsearcher".
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        if not collection:
            collection = self.default_collection
        try:
            self.client.drop_collection(collection)
        except Exception as exc:
            log.warning(log.safe_exception_message("oracle_clear", exc))
            raise


TABLES = {
    "DEEPSEARCHER_COLLECTION_INFO": """CREATE TABLE DEEPSEARCHER_COLLECTION_INFO (    
        id INT generated by default as identity primary key,
        collection varchar(256),
        description CLOB,
        status NUMBER DEFAULT 1,
        createtime TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updatetime TIMESTAMP DEFAULT NULL)""",
    "DEEPSEARCHER_COLLECTION_ITEM": """CREATE TABLE DEEPSEARCHER_COLLECTION_ITEM (    
        id INT generated by default as identity primary key,
        collection varchar(256),
        embedding VECTOR,
        text CLOB,
        reference varchar(4000),
        metadata CLOB,
        status NUMBER DEFAULT 1,
        createtime TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updatetime TIMESTAMP DEFAULT NULL)""",
}

SQL_TEMPLATES = {
    "has_table": f"""SELECT table_name FROM all_tables 
        WHERE table_name in ({",".join([f"'{k}'" for k in TABLES.keys()])})""",
    "has_collection": "select count(*) as rowcnt from DEEPSEARCHER_COLLECTION_INFO where collection=:collection and status=1",
    "list_collections": "select collection,description from DEEPSEARCHER_COLLECTION_INFO where status=1",
    "get_collection_description": "select description from DEEPSEARCHER_COLLECTION_INFO where collection=:collection and status=1",
    "update_collection_description": "update DEEPSEARCHER_COLLECTION_INFO set description=:description, updatetime=CURRENT_TIMESTAMP where collection=:collection and status=1",
    "drop_collection": "update DEEPSEARCHER_COLLECTION_INFO set status=0 where collection=:collection and status=1",
    "drop_collection_item": "update DEEPSEARCHER_COLLECTION_ITEM set status=0 where collection=:collection and status=1",
    "insert_collection": """INSERT INTO DEEPSEARCHER_COLLECTION_INFO (collection,description) 
        values (:collection,:description)""",
    "insert": """INSERT INTO DEEPSEARCHER_COLLECTION_ITEM (collection,embedding,text,reference,metadata) 
        values (:collection,:embedding,:text,:reference,:metadata)""",
    "search": """SELECT * FROM 
        (SELECT t.*,
            VECTOR_DISTANCE(t.embedding,vector(:embedding_string,{dimension},{dtype}),COSINE) as distance
        FROM DEEPSEARCHER_COLLECTION_ITEM t 
        JOIN DEEPSEARCHER_COLLECTION_INFO c ON t.collection=c.collection 
        WHERE t.collection=:collection AND t.status=1 AND c.status=1)
        WHERE distance<:max_distance ORDER BY distance ASC FETCH FIRST :top_k ROWS ONLY""",
}
