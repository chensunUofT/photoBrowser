from flask import render_template, redirect, url_for, request, g, session
from app import webapp

import mysql.connector
import tempfile
import os
from app import connectS3

from wand.image import Image

from app.config import db_config, s3_config_arg
import os, re, os.path

ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg', 'gif'])


def connect_to_database():
    return mysql.connector.connect(user=db_config['user'],
                                   password=db_config['password'],
                                   host=db_config['host'],
                                   database=db_config['database'])


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = connect_to_database()
    return db


@webapp.teardown_appcontext
def teardown_db(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


@webapp.route('/', methods=['GET'])
@webapp.route('/album', methods=['GET'])
# Return html with thumbnails of all photos for the current user
def thumbnails():
    if 'authenticated' not in session:
        return redirect(url_for('login'))

    cnx = get_db()

    cursor = cnx.cursor()

    query = "SELECT p.id, t.filename " \
            "FROM photo p, transformation t " \
            "WHERE p.id = t.photo_id AND " \
            "      t.type_id = 2 AND " \
            "      p.user_id = %s "

    try:
        cursor.execute(query, (session['user_id'],))
    except Exception as e:
        return e.msg

    return render_template("photos/album.html", cursor=cursor)


@webapp.route('/photo/<int:photo_id>', methods=['GET'])
# Return html with alls the versions of a given photo
def details(photo_id):
    if 'authenticated' not in session:
        return redirect(url_for('login'))

    try:
        cnx = get_db()
        cursor = cnx.cursor()

        # create a new tuple for the photo and store the
        query = "SELECT t.filename " \
                "FROM transformation t, photo p " \
                "WHERE t.photo_id = p.id AND " \
                "      p.id = %s AND " \
                "      p.user_id = %s AND " \
                "      t.type_id <> 2"
        cursor.execute(query, (photo_id, session['user_id']))

    except Exception as e:
        return e.msg

    return render_template("photos/details.html", cursor=cursor)


@webapp.route('/upload_form', methods=['GET'])
# Returns an html form for uploading a new photo
def upload_form():
    if 'authenticated' not in session:
        return redirect(url_for('login'))

    e = None

    if 'error' in session:
        e = session['error']
        session.pop('error')

    return render_template("photos/upload_form.html", error=e)


# Helper function
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@webapp.route('/upload_save', methods=['POST'])
# Handles photo upload and the creation of a thumbnail and three transformations
def upload_save():
    if 'authenticated' not in session:
        return redirect(url_for('upload_form'))

    # check if the post request has the file part
    if 'uploadedfile' not in request.files:
        session['error'] = "Missing uploaded file"
        return redirect(url_for('upload_form'))

    new_file = request.files['uploadedfile']

    # if user does not select file, browser also
    # submit a empty part without filename
    if new_file.filename == '':
        session['error'] = 'Missing file name'
        return redirect(url_for('upload_form'))

    if not allowed_file(new_file.filename):
        session['error'] = 'File type not supported'
        return redirect(url_for('upload_form'))

    # store the temporary file for transformation, they will be deleted later
    fname = os.path.join('app/static/user_images', new_file.filename)
    new_file.save(fname)

    # usr_id = session['user_id']
    #up load the file to s3 and get the url of the file on s3
    # returner = connectS3.upload_file_to_s3(fname, '1779photobucket', new_file.filename)
    returner = connectS3.upload_file_to_s3(fname, s3_config_arg['S3_BUCKET'], new_file.filename, session['user_id'])
    print(returner)

    try:
        #connect to db
        cnx = get_db()
        cursor = cnx.cursor()

        # create a new tuple for the photo and store the
        query = "INSERT INTO photo (user_id) VALUES (%s)"
        cursor.execute(query, (session['user_id'],))

        # get id of newly created tuple
        query = "SELECT LAST_INSERT_ID()"
        cursor.execute(query)
        row = cursor.fetchone()
        photo_id = row[0]

        # store the path to the original image
        query = "INSERT INTO transformation (filename,type_id,photo_id) VALUES (%s,%s,%s)"
        filepathS3 = returner
        cursor.execute(query, (filepathS3, 1, photo_id))


        img = Image(filename=fname)
        #do transformation
        transform_image(img, new_file, photo_id, cursor)

        cursor.close()

        cnx.commit()
        #delete the temporary image file
        msg = delete_tmp_img()
        print(msg)

    except Exception as e:
        exception = e
        print(exception)
        cnx.rollback()

    return redirect(url_for('thumbnails'))


def delete_tmp_img():
    mypath = "app/static/user_images"
    try:
        for root, dirs, files in os.walk(mypath):
            for file in files:
                os.remove(os.path.join(root, file))
    except Exception as e:
        print("Fail to delete:")
        return e
    return "file deleted"



#img: Image object
#new_file: file object
def transform_image(img, new_file, photo_id, cursor):
    try:
        with img as original:
            with original.clone() as thumbnail:
                print("level2")
                thumbnail.transform(resize='x200')
                trans_id = 2
                filepath = os.path.join('app/static/user_images', 'thb_' + new_file.filename)
                thumbnail.save(filename=filepath)

                returner = connectS3.upload_file_to_s3(filepath, s3_config_arg['S3_BUCKET'], 'thb_' + new_file.filename,session['user_id'])
                print(returner)
                thb_fp_s3 = returner
                query = "INSERT INTO transformation (filename,type_id,photo_id) VALUES (%s,%s,%s)"
                cursor.execute(query, (thb_fp_s3, trans_id, photo_id))

            with original.clone() as black_and_white:
                print("level3")
                black_and_white.type = 'grayscale'
                filepath = os.path.join('app/static/user_images', 'bl_wt_' + new_file.filename)
                black_and_white.save(filename=filepath)
                trans_id = 3

                returner = connectS3.upload_file_to_s3(filepath, s3_config_arg['S3_BUCKET'], 'bl_wt_' + new_file.filename,
                                                       session['user_id'])
                print(returner)
                bl_wt_fp_s3 = returner
                query = "INSERT INTO transformation (filename,type_id,photo_id) VALUES (%s,%s,%s)"
                cursor.execute(query, (bl_wt_fp_s3, trans_id, photo_id))

            with original.clone() as enhanced:
                # print("level4")
                enhanced.level(0.2, 0.9, 1.2)
                filename = os.path.join('app/static/user_images', 'eh_' + new_file.filename)
                enhanced.save(filename=filename)
                trans_id = 4

                returner = connectS3.upload_file_to_s3(filename, s3_config_arg['S3_BUCKET'], 'eh_' + new_file.filename,
                                                       session['user_id'])
                print(returner)
                eh_fp_s3 = returner
                query = "INSERT INTO transformation (filename,type_id,photo_id) VALUES (%s,%s,%s)"
                cursor.execute(query, (eh_fp_s3, trans_id, photo_id))
            with original.clone() as flopped:
                # print("level5")
                flopped.flop()
                filename = os.path.join('app/static/user_images', 'flp_' + new_file.filename)
                flopped.save(filename=filename)
                trans_id = 5
                returner = connectS3.upload_file_to_s3(filename, s3_config_arg['S3_BUCKET'], 'flp_' + new_file.filename,
                                                       session['user_id'])
                print(returner)
                flp_fp_s3 = returner
                query = "INSERT INTO transformation (filename,type_id,photo_id) VALUES (%s,%s,%s)"
                cursor.execute(query, (flp_fp_s3, trans_id, photo_id))
    except Exception as e:
        print("oh no Something Happened: ", e)
        return e

@webapp.route('/test/FileUpload', methods=['POST'])
# Entry point for automatic testing
def test_upload():
    cnx = get_db()

    cursor = cnx.cursor()

    query = "SELECT * FROM user WHERE username = %s"

    cursor.execute(query, (request.form['userID'],))

    row = cursor.fetchone()

    if row != None:
        session['authenticated'] = True
        session['username'] = request.form['userID']
        session['user_id'] = row[0]
        upload_save()
        return "OK"

    return "Error"